from urllib.parse import urlparse

import requests
from flask import (
    render_template,
    redirect,
    request,
    url_for,
    session,
    flash,
    abort,
    current_app
)

from flask_login import (
    login_required,
    current_user
)

from notifications_utils.field import Field
from notifications_python_client.errors import HTTPError

from app import service_api_client
from app.main import main
from app.utils import user_has_permissions, email_safe, get_cdn_domain
from app.main.forms import (
    ConfirmPasswordForm,
    ServiceNameForm,
    RequestToGoLiveForm,
    ServiceReplyToEmailFrom,
    ServiceSmsSender,
    ServiceLetterContactBlock,
    ServiceBrandingOrg,
    LetterBranding,
    ServiceInboundApiForm)
from app import user_api_client, current_service, organisations_client, inbound_number_client


dummy_bearer_token = 'bearer_token_set'


def get_inbound_api():
    if current_service['inbound_api']:
        return service_api_client.get_service_inbound_api(
            current_service['id'],
            current_service.get('inbound_api')[0]
        )


@main.route("/services/<service_id>/service-settings")
@login_required
@user_has_permissions('manage_settings', admin_override=True)
def service_settings(service_id):
    letter_branding_organisations = organisations_client.get_letter_organisations()
    if current_service['organisation']:
        organisation = organisations_client.get_organisation(current_service['organisation'])['organisation']
    else:
        organisation = None

    inbound_api = get_inbound_api()
    if inbound_api:
        parsed_url = urlparse(inbound_api.get('url')) if inbound_api else ''
        inbound_api_url = '{uri.scheme}://{uri.netloc}{elide_token}'.format(
            uri=parsed_url, elide_token='...' if parsed_url.path else '')
    else:
        inbound_api_url = ''

    inbound_number = inbound_number_client.get_inbound_sms_number_for_service(service_id)
    disp_inbound_number = inbound_number['data'].get('number', '')

    return render_template(
        'views/service-settings.html',
        organisation=organisation,
        letter_branding=letter_branding_organisations.get(
            current_service.get('dvla_organisation', '001')
        ),
        can_receive_inbound=('inbound_sms' in current_service['permissions']),
        inbound_api_url=inbound_api_url,
        letter_contact_block=Field(current_service['letter_contact_block'], html='escape'),
        inbound_number=disp_inbound_number
    )


@main.route("/services/<service_id>/service-settings/name", methods=['GET', 'POST'])
@login_required
@user_has_permissions('manage_settings', admin_override=True)
def service_name_change(service_id):
    form = ServiceNameForm()

    if request.method == 'GET':
        form.name.data = current_service.get('name')

    if form.validate_on_submit():
        unique_name = service_api_client.is_service_name_unique(form.name.data, email_safe(form.name.data))
        if not unique_name:
            form.name.errors.append("This service name is already in use")
            return render_template('views/service-settings/name.html', form=form)
        session['service_name_change'] = form.name.data
        return redirect(url_for('.service_name_change_confirm', service_id=service_id))

    return render_template(
        'views/service-settings/name.html',
        form=form)


@main.route("/services/<service_id>/service-settings/name/confirm", methods=['GET', 'POST'])
@login_required
@user_has_permissions('manage_settings', admin_override=True)
def service_name_change_confirm(service_id):
    # Validate password for form
    def _check_password(pwd):
        return user_api_client.verify_password(current_user.id, pwd)

    form = ConfirmPasswordForm(_check_password)

    if form.validate_on_submit():
        try:
            service_api_client.update_service(
                current_service['id'],
                name=session['service_name_change'],
                email_from=email_safe(session['service_name_change'])
            )
        except HTTPError as e:
            error_msg = "Duplicate service name '{}'".format(session['service_name_change'])
            if e.status_code == 400 and error_msg in e.message['name']:
                # Redirect the user back to the change service name screen
                flash('This service name is already in use', 'error')
                return redirect(url_for('main.service_name_change', service_id=service_id))
            else:
                raise e
        else:
            session.pop('service_name_change')
            return redirect(url_for('.service_settings', service_id=service_id))
    return render_template(
        'views/service-settings/confirm.html',
        heading='Change your service name',
        form=form)


@main.route("/services/<service_id>/service-settings/request-to-go-live", methods=['GET', 'POST'])
@login_required
@user_has_permissions('manage_settings', admin_override=True)
def service_request_to_go_live(service_id):
    form = RequestToGoLiveForm()

    if form.validate_on_submit():

        data = {
            'person_email': current_user.email_address,
            'person_name': current_user.name,
            'department_id': current_app.config.get('DESKPRO_DEPT_ID'),
            'agent_team_id': current_app.config.get('DESKPRO_ASSIGNED_AGENT_TEAM_ID'),
            'subject': 'Request to go live - {}'.format(current_service['name']),
            'message': (
                'On behalf of {} ({})\n\nExpected usage\n---'
                '\nMOU in place: {}'
                '\nChannel: {}\nStart date: {}\nStart volume: {}'
                '\nPeak volume: {}\nUpload or API: {}'
            ).format(
                current_service['name'],
                url_for('main.service_dashboard', service_id=current_service['id'], _external=True),
                form.mou.data,
                form.channel.data,
                form.start_date.data,
                form.start_volume.data,
                form.peak_volume.data,
                form.upload_or_api.data

            )
        }
        headers = {
            "X-DeskPRO-API-Key": current_app.config.get('DESKPRO_API_KEY'),
            'Content-Type': "application/x-www-form-urlencoded"
        }
        resp = requests.post(
            current_app.config.get('DESKPRO_API_HOST') + '/api/tickets',
            data=data,
            headers=headers
        )
        if resp.status_code != 201:
            current_app.logger.error(
                "Deskpro create ticket request failed with {} '{}'".format(
                    resp.status_code,
                    resp.json())
            )
            abort(500, "Request to go live submission failed")

        flash('We’ve received your request to go live', 'default')
        return redirect(url_for('.service_settings', service_id=service_id))

    return render_template('views/service-settings/request-to-go-live.html', form=form)


@main.route("/services/<service_id>/service-settings/switch-live")
@login_required
@user_has_permissions(admin_override=True)
def service_switch_live(service_id):
    service_api_client.update_service(
        current_service['id'],
        # TODO This limit should be set depending on the agreement signed by
        # with Notify.
        message_limit=250000 if current_service['restricted'] else 50,
        restricted=(not current_service['restricted'])
    )
    return redirect(url_for('.service_settings', service_id=service_id))


@main.route("/services/<service_id>/service-settings/research-mode")
@login_required
@user_has_permissions(admin_override=True)
def service_switch_research_mode(service_id):
    service_api_client.update_service_with_properties(
        service_id,
        {"research_mode": not current_service['research_mode']}
    )
    return redirect(url_for('.service_settings', service_id=service_id))


def switch_service_permissions(service_id, permission, sms_sender=None):
    permissions = current_service['permissions'].copy()
    if permission in permissions:
        permissions.remove(permission)
    else:
        permissions.append(permission)
    current_service['permissions'] = permissions

    data = {'permissions': permissions}
    if sms_sender:
        data['sms_sender'] = sms_sender
    service_api_client.update_service_with_properties(service_id, data)


@main.route("/services/<service_id>/service-settings/can-send-letters")
@login_required
@user_has_permissions(admin_override=True)
def service_switch_can_send_letters(service_id):
    switch_service_permissions(service_id, 'letter')
    return redirect(url_for('.service_settings', service_id=service_id))


@main.route("/services/<service_id>/service-settings/can-send-international-sms")
@login_required
@user_has_permissions(admin_override=True)
def service_switch_can_send_international_sms(service_id):
    switch_service_permissions(service_id, 'international_sms')
    return redirect(url_for('.service_settings', service_id=service_id))


@main.route("/services/<service_id>/service-settings/can-send-email")
@login_required
@user_has_permissions(admin_override=True)
def service_switch_can_send_email(service_id):
    switch_service_permissions(service_id, 'email')
    return redirect(url_for('.service_settings', service_id=service_id))


@main.route("/services/<service_id>/service-settings/can-send-sms")
@login_required
@user_has_permissions(admin_override=True)
def service_switch_can_send_sms(service_id):
    switch_service_permissions(service_id, 'sms')
    return redirect(url_for('.service_settings', service_id=service_id))


@main.route("/services/<service_id>/service-settings/archive", methods=['GET', 'POST'])
@login_required
@user_has_permissions('manage_settings', admin_override=True)
def archive_service(service_id):
    if request.method == 'POST':
        service_api_client.archive_service(service_id)
        return redirect(url_for('.service_settings', service_id=service_id))
    else:
        flash('There\'s no way to reverse this! Are you sure you want to archive this service?', 'delete')
        return service_settings(service_id)


@main.route("/services/<service_id>/service-settings/suspend", methods=["GET", "POST"])
@login_required
@user_has_permissions('manage_settings', admin_override=True)
def suspend_service(service_id):
    if request.method == 'POST':
        service_api_client.suspend_service(service_id)
        return redirect(url_for('.service_settings', service_id=service_id))
    else:
        flash("This will suspend the service and revoke all api keys. Are you sure you want to suspend this service?",
              'suspend')
        return service_settings(service_id)


@main.route("/services/<service_id>/service-settings/resume", methods=["GET", "POST"])
@login_required
@user_has_permissions('manage_settings', admin_override=True)
def resume_service(service_id):
    if request.method == 'POST':
        service_api_client.resume_service(service_id)
        return redirect(url_for('.service_settings', service_id=service_id))
    else:
        flash("This will resume the service. New api key are required for this service to use the API.", 'resume')
        return service_settings(service_id)


@main.route("/services/<service_id>/service-settings/set-email", methods=['GET'])
@login_required
@user_has_permissions('manage_settings', admin_override=True)
def service_set_email(service_id):
    return render_template(
        'views/service-settings/set-email.html',
    )


@main.route("/services/<service_id>/service-settings/set-reply-to-email", methods=['GET', 'POST'])
@login_required
@user_has_permissions('manage_settings', admin_override=True)
def service_set_reply_to_email(service_id):
    form = ServiceReplyToEmailFrom()
    if request.method == 'GET':
        form.email_address.data = current_service.get('reply_to_email_address')
    if form.validate_on_submit():
        service_api_client.update_service(
            current_service['id'],
            reply_to_email_address=form.email_address.data
        )
        return redirect(url_for('.service_settings', service_id=service_id))

    return render_template(
        'views/service-settings/set-reply-to-email.html',
        form=form)


@main.route("/services/<service_id>/service-settings/set-sms-sender", methods=['GET', 'POST'])
@login_required
@user_has_permissions('manage_settings', admin_override=True)
def service_set_sms_sender(service_id):
    form = ServiceSmsSender()
    if form.validate_on_submit():
        if 'inbound_sms' in current_service['permissions']:
            abort(403)
        service_api_client.update_service(
            current_service['id'],
            sms_sender=form.sms_sender.data or None
            )
        return redirect(url_for('.service_settings', service_id=service_id))

    if request.method == 'GET':
        form.sms_sender.data = current_service.get('sms_sender')

    return render_template(
        'views/service-settings/set-sms-sender.html',
        form=form)


@main.route("/services/<service_id>/service-settings/set-inbound-number", methods=['GET'])
@login_required
@user_has_permissions('manage_settings', admin_override=True)
def service_set_inbound_number(service_id):
    if 'inbound_sms' in current_service['permissions']:
        return redirect(url_for('.service_settings', service_id=service_id))

    switch_service_permissions(current_service['id'], 'inbound_sms')
    try:
        inbound_number_client.activate_inbound_sms_service(service_id)
        return redirect(url_for('.service_settings', service_id=service_id))
    except HTTPError as e:
        switch_service_permissions(current_service['id'], 'inbound_sms')
        raise e


@main.route("/services/<service_id>/service-settings/set-sms", methods=['GET'])
@login_required
@user_has_permissions('manage_settings', admin_override=True)
def service_set_sms(service_id):
    return render_template(
        'views/service-settings/set-sms.html',
    )


@main.route("/services/<service_id>/service-settings/set-international-sms", methods=['GET'])
@login_required
@user_has_permissions('manage_settings', admin_override=True)
def service_set_international_sms(service_id):
    return render_template(
        'views/service-settings/set-international-sms.html',
    )


@main.route("/services/<service_id>/service-settings/set-inbound-sms", methods=['GET'])
@login_required
@user_has_permissions('manage_settings', admin_override=True)
def service_set_inbound_sms(service_id):
    number = inbound_number_client.get_inbound_sms_number_for_service(service_id)['data'].get('number', '')
    return render_template(
        'views/service-settings/set-inbound-sms.html',
        inbound_number=number,
    )


@main.route("/services/<service_id>/service-settings/set-letters", methods=['GET'])
@login_required
@user_has_permissions('manage_settings', admin_override=True)
def service_set_letters(service_id):
    return render_template(
        'views/service-settings/set-letters.html',
    )


@main.route("/services/<service_id>/service-settings/set-letter-contact-block", methods=['GET', 'POST'])
@login_required
@user_has_permissions('manage_settings', admin_override=True)
def service_set_letter_contact_block(service_id):

    if 'letter' not in current_service['permissions']:
        abort(403)

    form = ServiceLetterContactBlock(letter_contact_block=current_service['letter_contact_block'])
    if form.validate_on_submit():
        service_api_client.update_service(
            current_service['id'],
            letter_contact_block=form.letter_contact_block.data.replace('\r', '') or None
        )
        if request.args.get('from_template'):
            return redirect(
                url_for('.view_template', service_id=service_id, template_id=request.args.get('from_template'))
            )
        return redirect(url_for('.service_settings', service_id=service_id))
    return render_template(
        'views/service-settings/set-letter-contact-block.html',
        form=form
    )


@main.route("/services/<service_id>/service-settings/set-branding-and-org", methods=['GET', 'POST'])
@login_required
@user_has_permissions(admin_override=True)
def service_set_branding_and_org(service_id):
    organisations = organisations_client.get_organisations()

    form = ServiceBrandingOrg(branding_type=current_service.get('branding'))
    # dynamically create org choices, including the null option
    form.organisation.choices = [('None', 'None')] + get_branding_as_value_and_label(organisations)

    if form.validate_on_submit():
        organisation = None if form.organisation.data == 'None' else form.organisation.data
        service_api_client.update_service(
            service_id,
            branding=form.branding_type.data,
            organisation=organisation
        )
        return redirect(url_for('.service_settings', service_id=service_id))

    form.organisation.data = current_service['organisation'] or 'None'

    return render_template(
        'views/service-settings/set-branding-and-org.html',
        form=form,
        branding_dict=get_branding_as_dict(organisations)
    )


@main.route("/services/<service_id>/service-settings/set-letter-branding", methods=['GET', 'POST'])
@login_required
@user_has_permissions(admin_override=True)
def set_letter_branding(service_id):

    form = LetterBranding(choices=organisations_client.get_letter_organisations().items())

    if form.validate_on_submit():
        service_api_client.update_service(
            service_id,
            dvla_organisation=form.dvla_org_id.data
        )
        return redirect(url_for('.service_settings', service_id=service_id))

    form.dvla_org_id.data = current_service.get('dvla_organisation', '001')

    return render_template(
        'views/service-settings/set-letter-branding.html',
        form=form,
    )


def get_branding_as_value_and_label(organisations):
    return [
        (organisation['id'], organisation['name'])
        for organisation in organisations
    ]


def get_branding_as_dict(organisations):
    return {
        organisation['id']: {
            'logo': 'https://{}/{}'.format(get_cdn_domain(), organisation['logo']),
            'colour': organisation['colour']
        } for organisation in organisations
    }


@main.route("/services/<service_id>/service-settings/set-inbound-api", methods=['GET', 'POST'])
@login_required
@user_has_permissions('manage_settings', admin_override=True)
def service_set_inbound_api(service_id):
    if 'inbound_sms' not in current_service['permissions']:
        abort(403)

    inbound_api = get_inbound_api()
    form = ServiceInboundApiForm(
        url=inbound_api.get('url') if inbound_api else '',
        bearer_token=dummy_bearer_token if inbound_api else ''
    )

    if form.validate_on_submit():
        if inbound_api:
            if inbound_api.get('url') != form.url.data or form.bearer_token.data != dummy_bearer_token:
                service_api_client.update_service_inbound_api(
                    service_id,
                    url=form.url.data,
                    bearer_token=form.bearer_token.data if form.bearer_token.data != dummy_bearer_token else '',
                    user_id=current_user.id,
                    inbound_api_id=inbound_api.get('id')
                )
        else:
            service_api_client.create_service_inbound_api(
                service_id,
                url=form.url.data,
                bearer_token=form.bearer_token.data,
                user_id=current_user.id
            )
        return redirect(url_for('.service_settings', service_id=service_id))

    return render_template(
        'views/service-settings/set-inbound-api.html',
        form=form,
    )
