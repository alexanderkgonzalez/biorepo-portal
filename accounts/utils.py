import re
import logging

from django.template.loader import get_template
from django.core.cache import cache
from django.core.mail import mail_admins
from django.contrib.auth import get_user_model
from django.views.decorators.debug import sensitive_variables

MAX_LOGIN_ATTEMPTS = 10
MAX_LOGIN_ATTEMPTS_KEY = '%s_%s_login_attempts'
MAX_LOGIN_ATTEMPTS_SUBJECT = 'Failed Login Attempt'

IP_RE = re.compile('\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}')

log = logging.getLogger('portal')

UserModel = get_user_model()


def get_ip_address(request):
    ip_address = request.META.get(
        'HTTP_X_FORWARDED_FOR', request.META.get('REMOTE_ADDR', None))

    if ip_address:
        ip_match = IP_RE.match(ip_address)
        if ip_match is not None:
            return ip_match.group()


@sensitive_variables('password')
def throttle_login(request):
    """
    Throttles a client by keeping track of the number of failed login
    attempts. If the user succeeds at a login before the max attempts has
    been reached, the attempts are reset.
    """
    email = request.POST.get('email')
    password = request.POST.get('password')

    # if the form is not filled out completely, pass along
    if not (email and password):
        return True

    ip_address = get_ip_address(request)

    log.debug('[authentication] login attempt from {0} - {1}'.format(ip_address, email.lower()))

    # the cache key is determined by the client IP address in addition to
    # the username being used to login. once the client reaches the max
    # login attempts, they will be notified.

    # if the same IP address attempts at multiple usernames within the same
    # session, the IP will be blacklisted.
    key = MAX_LOGIN_ATTEMPTS_KEY % (email.lower(), ip_address)
    attempts = cache.get(key, 0) + 1
    if attempts >= MAX_LOGIN_ATTEMPTS:

        # once the max attempts has been reached, deactive the account
        # and email the admins
        user_already_inactive = False

        try:
            user = UserModel.objects.get(email=email.lower())
        except UserModel.DoesNotExist:
            try:
                user = UserModel.objects.get(username=email.lower())
            except UserModel.DoesNotExist:
                return False

        if user:
            if user.is_active:
                user.is_active = False
                user.save()
            # this condition tells me whether the user attempted to access
            # their account with another session even though their account
            # is already inactive
            else:
                user_already_inactive = True

        t = get_template('accounts/max_login_attempts.txt')
        c = {
            'user': user,
            'username': email.lower(),
            'minutes': int(request.session.get_expiry_age() / 60.0),
            'email': email,
            'ip_address': ip_address,
            'user_already_inactive': user_already_inactive,
        }
        mail_admins(MAX_LOGIN_ATTEMPTS_SUBJECT, t.render(context=c), True)

        login_allowed = False

    else:
        login_allowed = True

    # cache for the duration of this session
    cache.set(key, attempts, request.session.get_expiry_age())

    # this is a convenience flag for subsequent requests during this session.
    # if the client clears their cookies or uses a different browser, this
    # flag will be gone, but the next login attempt will be caught again by
    # the above logic
    request.session['login_allowed'] = login_allowed

    return login_allowed


def clear_throttled_login(request):
    ip_address = get_ip_address(request)
    email = request.POST.get('email')
    # the cache key is determined by the client IP address in addition to
    # the username being used to login. once the client reaches the max
    # login attempts, they will be notified.

    # if the same IP address attempts at multiple usernames within the same
    # session, the IP will be blacklisted.
    key = MAX_LOGIN_ATTEMPTS_KEY % (email.lower(), ip_address)
    success = cache.delete(key)
    if success:
        log.error('delete cache login Key failed for {0} - {1}'.format(ip_address, email.lower()))
    log.debug('[authentication] login successful for {0}'.format(email.lower()))
    if 'login_allowed' in request.session:
        del request.session['login_allowed']
