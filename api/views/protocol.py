import logging
import json

from datetime import datetime
from copy import deepcopy

from django.core.cache import cache
from django.core.exceptions import ObjectDoesNotExist

from base import BRPApiView
from api.models.protocols import Organization, Protocol, ProtocolUserCredentials
from api.serializers import OrganizationSerializer, ProtocolSerializer, \
    eHBSubjectSerializer, ProtocolDataSourceSerializer, DataSourceSerializer
from api.utilities import SubjectUtils
from ehb_client.requests.exceptions import PageNotFound
from ehb_client.requests.subject_request_handler import Subject
from rest_framework.response import Response
from rest_framework import viewsets

logger = logging.getLogger(__name__)


class ProtocolViewSet(viewsets.ModelViewSet):
    """
    API endpoint that allows protocols to be viewed.
    """
    queryset = Protocol.objects.all()
    serializer_class = ProtocolSerializer

    def list(self, request, *args, **kwargs):
        protocols = []
        for p in Protocol.objects.all():
            if request.user in p.users.all():
                protocols.append(ProtocolSerializer(p, context={'request': request}).data)

        return Response(protocols)


class ProtocolDataSourceView(BRPApiView):
    def get(self, request, pk, *args, **kwargs):
        """
        Returns a list of protocol datasources associated with this protocol

        Also determines authorization for each protocol datasource based on
        the user making the request.
        """
        try:
            p = Protocol.objects.get(pk=pk)
        except ObjectDoesNotExist:
            return Response({'error': 'Protocol requested not found'}, status=404)

        ds = []

        for pds in p.getProtocolDataSources():
            t = ProtocolDataSourceSerializer(pds, context={'request': request}).data
            # Parse ProtocolDataSource configuration
            if pds.driver_configuration != '':
                dc = json.loads(pds.driver_configuration)
            else:
                dc = {}
            # If labels are defined get label names from eHB.
            # (label_id, label_description)
            if 'labels' in dc.keys():
                labels = cache.get('ehb_labels')
                if not labels:
                    labels = self.erl_rh.query()
                    cache.set('ehb_labels', labels)
                    cache.persist('ehb_labels')
                nl = []
                for l in dc['labels']:
                    for label in labels:
                        if l == label['id']:
                            if label['label'] == '':
                                nl.append((label['id'], 'Record'))
                            else:
                                nl.append((label['id'], label['label']))
                dc['labels'] = nl
            else:
                dc['labels'] = [(1, 'Record')]

            t["driver_configuration"] = dc
            # Determine Authorization
            try:
                ProtocolUserCredentials.objects.get(
                    protocol=p, data_source=pds, user=request.user)
                t["authorized"] = True
            except ProtocolUserCredentials.DoesNotExist:
                t["authorized"] = False
            # Include DataSource details
            t["data_source"] = DataSourceSerializer(pds.data_source).data

            ds.append(t)

        return Response(sorted(
            ds, key=lambda ds: ds["display_label"]))


class ProtocolOrganizationView(BRPApiView):
    def get(self, request, pk, *args, **kwargs):
        """
        Provide a list of organizations associated with a protocol
        """
        try:
            p = Protocol.objects.get(pk=pk)
        except ObjectDoesNotExist:
            return Response({'error': 'Protocol requested not found'}, status=404)

        if p.isUserAuthorized(request.user):
            q = p.organizations.all()
            orgs = [OrganizationSerializer(org).data for org in q]
        return Response(
            orgs,
            headers={'Access-Control-Allow-Origin': '*'}
        )


class ProtocolSubjectsView(BRPApiView):
    def get(self, request, pk, *args, **kwargs):
        """
        Returns a list of subjects associated with a protocol.
        """
        try:
            p = Protocol.objects.get(pk=pk)
        except ObjectDoesNotExist:
            return Response({'error': 'Protocol requested not found'}, status=404)
        # Check cache
        cache_data = cache.get('protocol{0}_sub_data'.format(p.id))
        if cache_data:
            return Response(
                json.loads(cache_data),
                headers={'Access-Control-Allow-Origin': '*'}
            )
        if p.isUserAuthorized(request.user):
            subjects = p.getSubjects()
            organizations = p.organizations.all()
            if subjects:
                subs = [eHBSubjectSerializer(sub).data for sub in subjects]
            else:
                return Response([])
            ehb_orgs = []
            # We can't rely on Ids being consistent across apps so we must
            # append the name here for display downstream.
            for o in organizations:
                ehb_orgs.append(o.getEhbServiceInstance())
            # Check if the protocol has external IDs configured. If so retrieve them
            manageExternalIDs = False

            protocoldatasources = p.getProtocolDataSources()

            for pds in protocoldatasources:
                if pds.driver == 3:
                    ExIdSource = pds
                    manageExternalIDs = True

            if manageExternalIDs:
                try:
                    config = json.loads(ExIdSource.driver_configuration)
                    if 'sort_on' in config.keys():
                        # er_label_rh = ServiceClient.get_rh_for(record_type=ServiceClient.EXTERNAL_RECORD_LABEL)
                        # lbl = er_label_rh.get(id=config['sort_on'])
                        lbl = ''
                        addl_id_column = lbl
                except:
                    pass

            for sub in subs:
                sub['external_records'] = []
                sub['external_ids'] = []
                for pds in protocoldatasources:
                    sub['external_records'].extend(pds.getSubjectExternalRecords(sub))
                if manageExternalIDs:
                    # Break out external ids into a separate object for ease of use
                    for record in sub['external_records']:
                        if record['external_system'] == 3:
                            sub['external_ids'].append(record)
                for ehb_org in ehb_orgs:
                    if sub['organization_id'] == ehb_org.id:
                        sub['organization_name'] = ehb_org.name
        else:
            return Response(
                {"detail": "You are not authorized to view subjects in this protocol"},
                status=403
            )

        if subjects:
            return Response(
                subs,
                headers={'Access-Control-Allow-Origin': '*'}
            )

        return Response([])


class ProtocolSubjectDetailView(BRPApiView):

    def post(self, request, pk, *args, **kwargs):
        '''
        Add a subject to the protocol

        Expects a request body of the form:
        {
            "first_name": "John",
            "last_name": "Doe",
            "organization_subject_id": "123123123",
            "organization": "1",
            "dob": "2000-01-01"
        }
        '''
        try:
            protocol = Protocol.objects.get(pk=pk)
        except ObjectDoesNotExist:
            return Response({'error': 'Protocol requested not found'}, status=404)

        subject = json.loads(request.body)

        new_subject = Subject(
            first_name=subject['first_name'],
            last_name=subject['last_name'],
            organization_id=int(subject['organization']),
            organization_subject_id=subject['organization_subject_id'],
            dob=datetime.strptime(subject['dob'], '%Y-%m-%d')
        )

        org = Organization.objects.get(pk=new_subject.organization_id)
        errors = []
        try:
            subject = self.s_rh.get(
                organization_id=new_subject.organization_id,
                organization_subject_id=new_subject.organization_subject_id)
            success = True
            # If found this indicates the subject is already in the ehb for
            # this organization, but not necessarily for this protocol.
            # That will be checked below in the external record search
            prefix = "A subject with this " + org.subject_id_label + " exists but with "
            if subject.first_name != new_subject.first_name:
                success = False
                errors.append(prefix + "first name: " + subject.first_name)
            if subject.last_name != new_subject.last_name:
                success = False
                errors.append(prefix + "last name: " + subject.last_name)
            if subject.dob != new_subject.dob.date():
                success = False
                errors.append(prefix + "birth date: " + str(subject.dob))
        except PageNotFound:
            # Subject is not in the system so create it
            r = self.s_rh.create(new_subject)[0]
            success = r.get('success')
            errors = r.get('errors')
            subject = r.get(Subject.identityLabel)

        # Dont proceed if creation was not a success
        if not success:
            subject = json.loads(Subject.json_from_identity(subject))
            return Response([success, subject, errors], status=422)

        if not errors:
            errors = []
        # First check if the subject is already in the group.
        if protocol.getSubjects() and subject in protocol.getSubjects():
            # Subject is already in protocol
            errors.append(
                'This subject ' + org.subject_id_label +
                ' has already been added to this project.'
            )
            logger.error("Could not add subject. They already exist on this protocol.")
            success = False
        else:
            # Add this subject to the protocol and create external record group
            if self.subject_utils.create_protocol_subject_record_group(protocol, new_subject):
                if protocol.addSubject(subject):
                    success = True
                else:
                    # Could not add subject to project
                    errors.append(
                        'Failed to complete eHB transactions. Could not add subject to project. Please try again.')
                    success = False
            else:
                # For some reason we couldn't get the eHB to add the subject to the protocol group
                errors.append(
                    'Failed to complete eHB transactions. Could not add subject to project. Please try again.')
                success = False

        subject = json.loads(Subject.json_from_identity(subject))

        if not success:
            return Response(
                [success, subject, errors],
                headers={'Access-Control-Allow-Origin': '*'},
                status=400
            )
        # Add subject to cache
        cache_key = 'protocol{0}_sub_data'.format(protocol.id)
        cache_data = self.cache.get(cache_key)
        if cache_data:
            subject['external_ids'] = []
            subject['external_records'] = []
            subject['organization_name'] = org.name
            subjects = json.loads(cache_data)
            subjects.append(subject)
            self.cache.set(cache_key, json.dumps(subjects))
            self.cache.persist(cache_key)
        return Response(
            [success, subject, errors],
            headers={'Access-Control-Allow-Origin': '*'},
            status=200
        )

    def get(self, request, pk, subject, *args, **kwargs):
        ''' get subject '''
        try:
            p = Protocol.objects.get(pk=pk)
        except ObjectDoesNotExist:
            return Response({'error': 'Protocol requested not found'}, status=404)

        if p.isUserAuthorized(request.user):
            protocoldatasources = p.getProtocolDataSources()
            manageExternalIDs = False
            for pds in protocoldatasources:
                if pds.driver == 3:
                    ExIdSource = pds
                    manageExternalIDs = True
            try:
                subject = self.s_rh.get(id=subject)
                organization = self.o_rh.get(id=subject.organization_id)
            except:
                return Response({'error': 'Subject not found'}, status=404)
            sub = json.loads(Subject.json_from_identity(subject))
            sub['organization_name'] = organization.name
            sub['external_records'] = []
            for pds in protocoldatasources:
                sub['external_records'].extend(pds.getSubjectExternalRecords(sub))
            if manageExternalIDs:
                # Break out external ids into a separate object for ease of use
                sub['external_ids'] = []
                for record in sub['external_records']:
                    if record['external_system'] == 3:
                        sub['external_ids'].append(record)
            return Response(sub)
        else:
            return Response(
                {"detail": "You are not authorized to view subjects in this protocol"},
                status=403
            )

    def put(self, request, *args, **kwargs):
        subject = json.loads(request.body)
        # See if subject exists
        try:
            ehb_sub = self.s_rh.get(id=subject['id'])
            org = Organization.objects.get(id=subject['organization_id'])
        except:
            return Response({'error': 'Subject not found'}, status=404)
        ehb_org = org.getEhbServiceInstance()
        ehb_sub.old_subject = deepcopy(ehb_sub)
        ehb_sub.first_name = subject['first_name']
        ehb_sub.last_name = subject['last_name']
        ehb_sub.organization_subject_id = subject['organization_subject_id']
        ehb_sub.organization_id = ehb_org.id
        ehb_sub.dob = datetime.strptime(subject['dob'], '%Y-%m-%d')
        update = self.s_rh.update(ehb_sub)[0]
        if update['errors']:
            return Response(json.dumps({'error': 'Unable to update subject'}), status=400)
        sub = json.loads(Subject.json_from_identity(update['subject']))
        sub['organization_name'] = org.name
        return Response(
            sub,
            headers={'Access-Control-Allow-Origin': '*'}
        )

    def delete(self, request, pk, subject, *args, **kwargs):
        try:
            subject = self.s_rh.get(id=subject)
            protocol = Protocol.objects.get(pk=pk)
            response = self.s_rh.delete(id=subject.id)
            success = SubjectUtils.delete_protocol_subject_record_group(protocol, subject)
        except:
            return Response({'error': 'Unable to delete subject'}, status=400)

        return Response({'info': 'Subject deleted'}, status=200)
