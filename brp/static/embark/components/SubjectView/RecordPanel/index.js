// jscs:disable requireCamelCaseOrUpperCaseIdentifiers
import React from 'react';
import SkyLight from 'react-skylight';
import PDSRecordGroup from './PDSRecordGroup';
import LinkModeBanner from './LinkModeBanner';
import * as SubjectActions from '../../../actions/subject';
import * as RecordActions from '../../../actions/record';
import { connect } from 'react-redux';

class RecordPanel extends React.Component {

  constructor(props) {
    super(props);
  }

  componentDidMount() {
    const { dispatch } = this.props;
    dispatch(SubjectActions.setActiveSubject(this.props.subject));
  }

  render() {
    const dispatch = this.props.dispatch;
    const pds = this.props.pds.items;
    const records = this.props.record.items;
    if (pds) {
      var pdsNodes = pds.map(function (pds, i) {
        var pds_records = records.filter(function (record) {
          if (pds.id == record.pds) {
            return record;
          }
        });

        return (
          <PDSRecordGroup key={i} pds={pds} records={pds_records}/>
        );

      }, this);
    }

    return (
      <div className="col-md-8 col-sm-2">
        <div className="card">
          <div className="content">
            { this.props.linkMode ? <LinkModeBanner /> : null }
            <h5 className="category">Subject Records</h5>
            { pdsNodes }
          </div>
        </div>
        { this.props.children }
      </div>
    );
  }
}

function mapStateToProps(state) {
  return {
    protocol: {
      items: state.protocol.items,
      activeProtocol: state.protocol.activeProtocol,
    },
    pds: {
      items: state.pds.items,
    },
    record: {
      items: state.record.items,
    },
    showInfoPanel: state.subject.showInfoPanel,
    showActionPanel: state.subject.showActionPanel,
    activeRecord: state.record.activeRecord,
    activeSubject: state.subject.activeSubject,
    activeSubjectRecords: state.subject.activeSubjectRecords,
    selectedLabel: state.record.selectedLabel,
    linkMode: state.subject.linkMode,
  };
}

export default connect(mapStateToProps)(RecordPanel);