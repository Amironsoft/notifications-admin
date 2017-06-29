# -*- coding: utf-8 -*-
import uuid
from io import BytesIO
from os import path
from glob import glob
from itertools import repeat
from functools import partial

import pytest
from bs4 import BeautifulSoup
from flask import url_for
from notifications_utils.template import LetterPreviewTemplate, LetterImageTemplate
from notifications_utils.recipients import RecipientCSV

from app.main.views.send import get_check_messages_back_url

from tests import validate_route_permission, template_json
from tests.app.test_utils import normalize_spaces
from tests.conftest import (
    mock_get_service_template,
    mock_get_service_template_with_placeholders,
    mock_get_service_letter_template,
    mock_get_service,
    mock_get_international_service,
    mock_get_service_template,
    mock_get_service_email_template,
    SERVICE_ONE_ID,
)

template_types = ['email', 'sms']

# The * ignores hidden files, eg .DS_Store
test_spreadsheet_files = glob(path.join('tests', 'spreadsheet_files', '*'))
test_non_spreadsheet_files = glob(path.join('tests', 'non_spreadsheet_files', '*'))


def test_that_test_files_exist():
    assert len(test_spreadsheet_files) == 8
    assert len(test_non_spreadsheet_files) == 6


@pytest.mark.parametrize(
    "filename, acceptable_file",
    list(zip(
        test_spreadsheet_files, repeat(True)
    )) +
    list(zip(
        test_non_spreadsheet_files, repeat(False)
    ))
)
def test_upload_files_in_different_formats(
    filename,
    acceptable_file,
    logged_in_client,
    service_one,
    mocker,
    mock_get_service_template,
    mock_s3_upload,
    fake_uuid,
):

    with open(filename, 'rb') as uploaded:
        response = logged_in_client.post(
            url_for('main.send_messages', service_id=service_one['id'], template_id=fake_uuid),
            data={'file': (BytesIO(uploaded.read()), filename)},
            content_type='multipart/form-data'
        )

    if acceptable_file:
        assert mock_s3_upload.call_args[0][1]['data'].strip() == (
            "phone number,name,favourite colour,fruit\r\n"
            "07739 468 050,Pete,Coral,tomato\r\n"
            "07527 125 974,Not Pete,Magenta,Avacado\r\n"
            "07512 058 823,Still Not Pete,Crimson,Pear"
        )
    else:
        assert not mock_s3_upload.called
        assert (
            'Couldn’t read {}. Try using a different file format.'.format(filename)
        ) in response.get_data(as_text=True)


def test_upload_csvfile_with_errors_shows_check_page_with_errors(
    logged_in_client,
    service_one,
    mocker,
    mock_get_service_template_with_placeholders,
    mock_s3_upload,
    mock_get_users_by_service,
    mock_get_detailed_service_for_today,
    fake_uuid,
):

    mocker.patch(
        'app.main.views.send.s3download',
        return_value="""
            phone number,name
            +447700900986
            +447700900986
        """
    )

    initial_upload = logged_in_client.post(
        url_for('main.send_messages', service_id=service_one['id'], template_id=fake_uuid),
        data={'file': (BytesIO(''.encode('utf-8')), 'invalid.csv')},
        content_type='multipart/form-data',
        follow_redirects=True
    )
    reupload = logged_in_client.post(
        url_for('main.check_messages', service_id=service_one['id'], template_type='sms', upload_id='abc123'),
        data={'file': (BytesIO(''.encode('utf-8')), 'invalid.csv')},
        content_type='multipart/form-data',
        follow_redirects=True
    )
    for response in [initial_upload, reupload]:
        assert response.status_code == 200
        content = response.get_data(as_text=True)
        assert 'There is a problem with your data' in content
        assert '+447700900986' in content
        assert 'Missing' in content
        assert 'Re-upload your file' in content


@pytest.mark.parametrize('file_contents, expected_error,', [
    (
        """
            telephone,name
            +447700900986
        """,
        (
            'Your file needs a column called ‘phone number’ '
            'Right now it has columns called ‘telephone’ and ‘name’. '
            'Skip to file contents'
        )
    ),
    (
        """
            phone number
            +447700900986
        """,
        (
            'The columns in your file need to match the double brackets in your template '
            'Your file is missing a column called ‘name’. '
            'Skip to file contents'
        )
    ),
    (
        "+447700900986",
        (
            'Your file is missing some rows '
            'It needs at least one row of data, and columns called ‘name’ and ‘phone number’. '
            'Skip to file contents'
        )
    ),
    (
        "",
        (
            'Your file is missing some rows '
            'It needs at least one row of data, and columns called ‘name’ and ‘phone number’. '
            'Skip to file contents'
        )
    ),
])
def test_upload_csvfile_with_missing_columns_shows_error(
    logged_in_client,
    mocker,
    mock_get_service_template_with_placeholders,
    mock_s3_upload,
    mock_get_users_by_service,
    mock_get_detailed_service_for_today,
    service_one,
    fake_uuid,
    file_contents,
    expected_error,
):

    mocker.patch('app.main.views.send.s3download', return_value=file_contents)

    response = logged_in_client.post(
        url_for('main.send_messages', service_id=service_one['id'], template_id=fake_uuid),
        data={'file': (BytesIO(''.encode('utf-8')), 'invalid.csv')},
        follow_redirects=True,
    )

    assert response.status_code == 200
    page = BeautifulSoup(response.data.decode('utf-8'), 'html.parser')
    assert ' '.join(page.select('.banner-dangerous')[0].text.split()) == expected_error


def test_upload_csv_invalid_extension(
    logged_in_client,
    mock_login,
    service_one,
    mock_get_service_template,
    fake_uuid,
):

    resp = logged_in_client.post(
        url_for('main.send_messages', service_id=service_one['id'], template_id=fake_uuid),
        data={'file': (BytesIO('contents'.encode('utf-8')), 'invalid.txt')},
        content_type='multipart/form-data',
        follow_redirects=True
    )

    assert resp.status_code == 200
    assert "invalid.txt isn’t a spreadsheet that Notify can read" in resp.get_data(as_text=True)


def test_upload_valid_csv_shows_file_contents(
    logged_in_client,
    mocker,
    mock_get_service_template_with_placeholders,
    mock_s3_upload,
    mock_get_users_by_service,
    mock_get_detailed_service_for_today,
    fake_uuid,
):

    mocker.patch('app.main.views.send.s3download', return_value="""
        phone number,name,thing,thing,thing
        07700900986, Jo,  foo,  foo,  foo
    """)

    response = logged_in_client.post(
        url_for('main.send_messages', service_id=SERVICE_ONE_ID, template_id=fake_uuid),
        data={'file': (BytesIO(''.encode('utf-8')), 'valid.csv')},
        follow_redirects=True,
    )

    assert response.status_code == 200
    page = BeautifulSoup(response.data.decode('utf-8'), 'html.parser')
    assert page.h1.text.strip() == 'Preview of Two week reminder'
    for index, cell in enumerate([
        '<td class="table-field-index"> <span class=""> 2 </span> </td>',
        '<td class="table-field-center-aligned "> <div class=""> 07700900986 </div> </td>',
        '<td class="table-field-center-aligned "> <div class=""> Jo </div> </td>',
        (
            '<td class="table-field-center-aligned "> '
            '<div class="table-field-status-default"> '
            '<ul class="list list-bullet"> '
            '<li>foo</li> <li>foo</li> <li>foo</li> '
            '</ul> '
            '</div> '
            '</td>'
        ),
    ]):
        assert normalize_spaces(str(page.select('table tbody td')[index])) == cell


def test_send_test_doesnt_show_file_contents(
    logged_in_client,
    mocker,
    mock_get_service_template,
    mock_s3_upload,
    mock_get_users_by_service,
    mock_get_detailed_service_for_today,
    service_one,
    fake_uuid,
):

    mocker.patch('app.main.views.send.s3download', return_value="""
        phone number
        07700 900 986
    """)

    response = logged_in_client.get(
        url_for('main.send_test', service_id=service_one['id'], template_id=fake_uuid),
        follow_redirects=True,
    )

    assert response.status_code == 200
    page = BeautifulSoup(response.data.decode('utf-8'), 'html.parser')
    assert page.select('h1')[0].text.strip() == 'Preview of Two week reminder'
    assert len(page.select('table')) == 0
    assert len(page.select('.banner-dangerous')) == 0
    assert page.select('input[type=submit]')[0]['value'].strip() == 'Send 1 text message'


def test_send_test_sms_message(
    logged_in_client,
    service_one,
    fake_uuid,
    mocker,
    mock_get_service_template,
    mock_s3_upload,
    mock_get_users_by_service,
    mock_get_detailed_service_for_today,
):

    mocker.patch('app.main.views.send.s3download', return_value='phone number\r\n+4412341234')

    response = logged_in_client.get(
        url_for('main.send_test', service_id=service_one['id'], template_id=fake_uuid),
        follow_redirects=True
    )
    assert response.status_code == 200
    mock_s3_upload.assert_called_with(
        service_one['id'],
        {'data': 'phone number\r\n07700 900762\r\n', 'file_name': 'Report'},
        'eu-west-1'
    )


@pytest.mark.parametrize('endpoint, template_mock, expected_session_contents', [
    ('main.send_test_step', mock_get_service_template_with_placeholders, {'phone number': '07700 900762'}),
    ('main.send_test_step', mock_get_service_email_template, {'email address': 'test@user.gov.uk'}),
    ('main.send_test_step', mock_get_service_letter_template, {}),
    ('main.send_one_off_step', mock_get_service_template, {}),
    ('main.send_one_off_step', mock_get_service_email_template, {}),
    ('main.send_one_off_step', mock_get_service_letter_template, {}),
])
def test_send_test_step_redirects_if_session_not_setup(
    mocker,
    logged_in_client,
    mock_get_detailed_service_for_today,
    mock_get_users_by_service,
    fake_uuid,
    endpoint,
    template_mock,
    expected_session_contents,
):

    template_mock(mocker)
    mocker.patch('app.main.views.send.get_page_count_for_letter', return_value=99)

    with logged_in_client.session_transaction() as session:
        assert 'send_test_values' not in session

    response = logged_in_client.get(
        url_for(endpoint, service_id=SERVICE_ONE_ID, template_id=fake_uuid, step_index=0),
        follow_redirects=True
    )
    assert response.status_code == 200

    with logged_in_client.session_transaction() as session:
        assert session['send_test_values'] == expected_session_contents


@pytest.mark.parametrize('template_mock, partial_url, expected_h1, tour_shown', [
    (
        mock_get_service_template_with_placeholders,
        partial(url_for, 'main.send_test'),
        'Send to one recipient',
        False,
    ),
    (
        mock_get_service_template_with_placeholders,
        partial(url_for, 'main.send_one_off'),
        'Send to one recipient',
        False,
    ),
    (
        mock_get_service_template_with_placeholders,
        partial(url_for, 'main.send_test', help=1),
        'Example text message',
        True,
    ),
    (
        mock_get_service_email_template,
        partial(url_for, 'main.send_test', help=1),
        'Example text message',
        True,
    ),
    (
        mock_get_service_email_template,
        partial(url_for, 'main.send_test'),
        'Send to one recipient',
        False,
    ),
    (
        mock_get_service_email_template,
        partial(url_for, 'main.send_one_off'),
        'Send to one recipient',
        False,
    ),
    (
        mock_get_service_letter_template,
        partial(url_for, 'main.send_test'),
        'Print a test letter',
        False,
    ),
    (
        mock_get_service_letter_template,
        partial(url_for, 'main.send_one_off'),
        'Print a test letter',
        False,
    ),
])
def test_send_one_off_or_test_has_correct_page_titles(
    logged_in_client,
    service_one,
    fake_uuid,
    mocker,
    template_mock,
    partial_url,
    expected_h1,
    tour_shown,
):

    template_mock(mocker)
    mocker.patch('app.main.views.send.get_page_count_for_letter', return_value=99)

    response = logged_in_client.get(
        partial_url(service_id=service_one['id'], template_id=fake_uuid, step_index=0),
        follow_redirects=True,
    )
    page = BeautifulSoup(response.data.decode('utf-8'), 'html.parser')

    assert response.status_code == 200
    assert page.h1.text.strip() == expected_h1

    assert (len(page.select('.banner-tour')) == 1) == tour_shown


@pytest.mark.parametrize('template_mock, expected_link_text, expected_link_url', [
    (mock_get_service_template, 'Use my phone number', partial(url_for, 'main.send_test')),
    (mock_get_service_email_template, 'Use my email address', partial(url_for, 'main.send_test')),
    (mock_get_service_letter_template, None, None),
])
def test_send_one_off_has_skip_link(
    logged_in_client,
    service_one,
    fake_uuid,
    mock_get_service_email_template,
    mocker,
    template_mock,
    expected_link_text,
    expected_link_url,
):

    template_mock(mocker)
    mocker.patch('app.main.views.send.get_page_count_for_letter', return_value=99)

    response = logged_in_client.get(
        url_for('main.send_one_off_step', service_id=service_one['id'], template_id=fake_uuid, step_index=0),
        follow_redirects=True
    )
    page = BeautifulSoup(response.data.decode('utf-8'), 'html.parser')
    skip_links = page.select('a.top-gutter-4-3')

    assert response.status_code == 200

    if expected_link_text and expected_link_url:
        assert skip_links[0].text.strip() == expected_link_text
        assert skip_links[0]['href'] == expected_link_url(
            service_id=service_one['id'],
            template_id=fake_uuid,
        )
    else:
        assert not skip_links


@pytest.mark.parametrize('endpoint, expected_redirect, send_test_values', [
    (
        'main.send_test_step',
        'main.send_test',
        {'name': 'foo'},
    ),
    (
        'main.send_one_off_step',
        'main.send_one_off',
        {'name': 'foo', 'phone number': '07900900123'},
    ),
])
def test_send_test_redirects_to_end_if_step_out_of_bounds(
    logged_in_client,
    service_one,
    fake_uuid,
    mock_get_service_template,
    mock_s3_upload,
    mock_get_users_by_service,
    mock_get_detailed_service_for_today,
    endpoint,
    send_test_values,
    expected_redirect,
):

    with logged_in_client.session_transaction() as session:
        session['send_test_values'] = send_test_values

    response = logged_in_client.get(url_for(
        endpoint,
        service_id=service_one['id'],
        template_id=fake_uuid,
        step_index=999,
    ))

    assert response.status_code == 302
    expected_url = url_for(
        'main.check_messages',
        service_id=service_one['id'],
        upload_id=fake_uuid,
        template_type='sms',
        _external=True,
    )
    assert response.location in (
        expected_url + '?help=0&from_test=True',
        expected_url + '?from_test=True&help=0',
    )


@pytest.mark.parametrize('endpoint, expected_redirect', [
    ('main.send_test_step', 'main.send_test'),
    ('main.send_one_off_step', 'main.send_one_off'),
])
def test_send_test_redirects_to_start_if_you_skip_steps(
    logged_in_platform_admin_client,
    service_one,
    fake_uuid,
    mock_get_service_letter_template,
    mock_s3_upload,
    mock_get_users_by_service,
    mock_get_detailed_service_for_today,
    mocker,
    endpoint,
    expected_redirect,
):

    with logged_in_platform_admin_client.session_transaction() as session:
        session['send_test_letter_page_count'] = 1
        session['send_test_values'] = {'address_line_1': 'foo'}

    response = logged_in_platform_admin_client.get(url_for(
        endpoint,
        service_id=service_one['id'],
        template_id=fake_uuid,
        step_index=7,  # letter template has 7 placeholders – we’re at the end
    ))
    assert response.status_code == 302
    assert response.location == url_for(
        expected_redirect,
        service_id=service_one['id'],
        template_id=fake_uuid,
        _external=True,
    )


@pytest.mark.parametrize('endpoint, expected_redirect', [
    ('main.send_test_step', 'main.send_test'),
    ('main.send_one_off_step', 'main.send_one_off'),
])
def test_send_test_redirects_to_start_if_index_out_of_bounds_and_some_placeholders_empty(
    logged_in_client,
    service_one,
    fake_uuid,
    mock_get_service_email_template,
    mock_s3_download,
    mock_get_users_by_service,
    mock_get_detailed_service_for_today,
    endpoint,
    expected_redirect,
):

    with logged_in_client.session_transaction() as session:
        session['send_test_values'] = {'name': 'foo'}

    response = logged_in_client.get(url_for(
        endpoint,
        service_id=service_one['id'],
        template_id=fake_uuid,
        step_index=999,
    ))

    assert response.status_code == 302
    assert response.location == url_for(
        expected_redirect,
        service_id=service_one['id'],
        template_id=fake_uuid,
        _external=True,
    )


@pytest.mark.parametrize('endpoint, expected_redirect', [
    ('main.send_test', 'main.send_test_step'),
    ('main.send_one_off', 'main.send_one_off_step'),
])
def test_send_test_sms_message_redirects_with_help_argument(
    logged_in_client,
    service_one,
    fake_uuid,
    endpoint,
    expected_redirect,
):
    response = logged_in_client.get(
        url_for(endpoint, service_id=service_one['id'], template_id=fake_uuid, help=1)
    )
    assert response.status_code == 302
    assert response.location == url_for(
        expected_redirect,
        service_id=service_one['id'],
        template_id=fake_uuid,
        step_index=0,
        help=1,
        _external=True,
    )


def test_send_test_email_message_without_placeholders(
    logged_in_client,
    mocker,
    service_one,
    mock_get_service_email_template_without_placeholders,
    mock_s3_upload,
    mock_get_users_by_service,
    mock_get_detailed_service_for_today,
    fake_uuid,
):

    mocker.patch('app.main.views.send.s3download', return_value='email address\r\ntest@user.gov.uk')

    response = logged_in_client.get(
        url_for('main.send_test', service_id=service_one['id'], template_id=fake_uuid),
        follow_redirects=True
    )
    assert response.status_code == 200
    mock_s3_upload.assert_called_with(
        service_one['id'],
        {'data': 'email address\r\ntest@user.gov.uk\r\n', 'file_name': 'Report'},
        'eu-west-1'
    )


def test_send_test_sms_message_with_placeholders_shows_first_field(
    logged_in_client,
    mocker,
    service_one,
    mock_login,
    mock_get_service,
    mock_get_service_template_with_placeholders,
    fake_uuid,
):

    with logged_in_client.session_transaction() as session:
        assert 'send_test_values' not in session

    response = logged_in_client.get(
        url_for(
            'main.send_test',
            service_id=service_one['id'],
            template_id=fake_uuid,
        ),
        follow_redirects=True,
    )
    assert response.status_code == 200
    page = BeautifulSoup(response.data.decode('utf-8'), 'html.parser')

    assert page.select('label')[0].text.strip() == 'name'
    assert page.select('input')[0]['name'] == 'placeholder_value'
    assert page.select('.page-footer-back-link')[0]['href'] == url_for(
        'main.view_template',
        service_id=service_one['id'],
        template_id=fake_uuid,
    )
    with logged_in_client.session_transaction() as session:
        assert session['send_test_values']['phone number'] == '07700 900762'


def test_send_test_letter_clears_previous_page_cache(
    logged_in_platform_admin_client,
    mocker,
    service_one,
    mock_login,
    mock_get_service,
    mock_get_service_letter_template,
    fake_uuid,
):

    with logged_in_platform_admin_client.session_transaction() as session:
        session['send_test_letter_page_count'] = 'WRONG'

    response = logged_in_platform_admin_client.get(url_for(
        'main.send_test',
        service_id=service_one['id'],
        template_id=fake_uuid,
    ))
    assert response.status_code == 302

    with logged_in_platform_admin_client.session_transaction() as session:
        assert session['send_test_letter_page_count'] is None


def test_send_test_populates_field_from_session(
    logged_in_client,
    mocker,
    service_one,
    mock_login,
    mock_get_service,
    mock_get_service_template_with_placeholders,
    fake_uuid,
):

    with logged_in_client.session_transaction() as session:
        session['send_test_values'] = {}
        session['send_test_values']['name'] = 'Jo'

    response = logged_in_client.get(url_for(
        'main.send_test_step',
        service_id=service_one['id'],
        template_id=fake_uuid,
        step_index=0,
    ))
    assert response.status_code == 200
    page = BeautifulSoup(response.data.decode('utf-8'), 'html.parser')

    assert page.select('input')[0]['value'] == 'Jo'


def test_send_test_caches_page_count(
    logged_in_client,
    mocker,
    service_one,
    mock_login,
    mock_get_service,
    mock_get_service_letter_template,
    fake_uuid,
):

    mocker.patch('app.main.views.send.get_page_count_for_letter', return_value=99)

    response = logged_in_client.get(
        url_for(
            'main.send_test',
            service_id=service_one['id'],
            template_id=fake_uuid,
        ),
        follow_redirects=True,
    )
    with logged_in_client.session_transaction() as session:
        assert session['send_test_letter_page_count'] == 99


def test_send_test_indicates_optional_address_columns(
    logged_in_client,
    mocker,
    service_one,
    mock_login,
    mock_get_service,
    mock_get_service_letter_template,
    fake_uuid,
):

    mocker.patch('app.main.views.send.get_page_count_for_letter', return_value=1)

    with logged_in_client.session_transaction() as session:
        session['send_test_values'] = {}

    response = logged_in_client.get(url_for(
        'main.send_test_step',
        service_id=service_one['id'],
        template_id=fake_uuid,
        step_index=3,
    ))
    assert response.status_code == 200
    page = BeautifulSoup(response.data.decode('utf-8'), 'html.parser')

    assert normalize_spaces(page.select('label')[0].text) == (
        'address line 4 '
        'Optional'
    )
    assert page.select('.page-footer-back-link')[0]['href'] == url_for(
        'main.send_test_step',
        service_id=service_one['id'],
        template_id=fake_uuid,
        step_index=2,
    )


def test_send_test_allows_empty_optional_address_columns(
    logged_in_client,
    mocker,
    service_one,
    mock_login,
    mock_get_service,
    mock_get_service_letter_template,
    fake_uuid,
):

    mocker.patch('app.main.views.send.get_page_count_for_letter', return_value=1)

    with logged_in_client.session_transaction() as session:
        session['send_test_values'] = {}

    response = logged_in_client.post(
        url_for(
            'main.send_test_step',
            service_id=service_one['id'],
            template_id=fake_uuid,
            step_index=3,
        ),
        # no data here
    )

    assert response.status_code == 302
    assert response.location == url_for(
        'main.send_test_step',
        service_id=service_one['id'],
        template_id=fake_uuid,
        step_index=4,
        _external=True,
    )


def test_send_test_sms_message_puts_submitted_data_in_session_and_file(
    logged_in_client,
    mocker,
    service_one,
    mock_get_service_template_with_placeholders,
    mock_s3_upload,
    mock_get_users_by_service,
    mock_get_detailed_service_for_today,
    fake_uuid,
):

    with logged_in_client.session_transaction() as session:
        session['send_test_values'] = {}

    mocker.patch('app.main.views.send.s3download', return_value='phone number\r\n+4412341234')

    response = logged_in_client.post(
        url_for(
            'main.send_test_step',
            service_id=service_one['id'],
            template_id=fake_uuid,
            step_index=0,
        ),
        data={'placeholder_value': 'Jo'},
        follow_redirects=True,
    )
    assert response.status_code == 200

    with logged_in_client.session_transaction() as session:
        assert session['send_test_values']['phone number'] == '07700 900762'
        assert session['send_test_values']['name'] == 'Jo'

    mock_s3_upload.assert_called_with(
        service_one['id'],
        {
            'data': 'name,phone number\r\nJo,07700 900762\r\n',
            'file_name': 'Report'
        },
        'eu-west-1'
    )


@pytest.mark.parametrize('filetype', ['pdf', 'png'])
def test_send_test_works_as_letter_preview(
    filetype,
    logged_in_platform_admin_client,
    mock_get_service_letter_template,
    mock_get_users_by_service,
    mock_get_detailed_service_for_today,
    service_one,
    fake_uuid,
    mocker,
):
    service_one['permissions'] = ['letter']
    mocker.patch('app.service_api_client.get_service', return_value={"data": service_one})
    mocker.patch('app.main.views.send.get_page_count_for_letter', return_value=1)
    mocked_preview = mocker.patch(
        'app.main.views.send.TemplatePreview.from_utils_template',
        return_value='foo'
    )

    service_id = service_one['id']
    template_id = fake_uuid
    with logged_in_platform_admin_client.session_transaction() as session:
        session['send_test_values'] = {'address_line_1': 'Jo Lastname'}
    response = logged_in_platform_admin_client.get(
        url_for(
            'main.send_test_preview',
            service_id=service_id,
            template_id=template_id,
            filetype=filetype
        )
    )

    mock_get_service_letter_template.assert_called_with(service_id, template_id)

    assert response.status_code == 200
    assert response.get_data(as_text=True) == 'foo'
    assert mocked_preview.call_args[0][0].id == template_id
    assert type(mocked_preview.call_args[0][0]) == LetterImageTemplate
    assert mocked_preview.call_args[0][0].values == {'address_line_1': 'Jo Lastname'}
    assert mocked_preview.call_args[0][1] == filetype


def test_send_test_clears_session(
    logged_in_client,
    mocker,
    service_one,
    fake_uuid,
):

    with logged_in_client.session_transaction() as session:
        session['send_test_values'] = {'foo': 'bar'}

    response = logged_in_client.get(
        url_for(
            'main.send_test',
            service_id=service_one['id'],
            template_id=fake_uuid,
        ),
    )
    assert response.status_code == 302

    with logged_in_client.session_transaction() as session:
        assert session['send_test_values'] == {}


def test_download_example_csv(
    logged_in_client,
    mocker,
    api_user_active,
    mock_login,
    mock_get_service,
    mock_get_service_template,
    mock_has_permissions,
    fake_uuid
):

    response = logged_in_client.get(
        url_for('main.get_example_csv', service_id=fake_uuid, template_id=fake_uuid),
        follow_redirects=True
    )
    assert response.status_code == 200
    assert response.get_data(as_text=True) == 'phone number\r\n07700 900321\r\n'
    assert 'text/csv' in response.headers['Content-Type']


def test_upload_csvfile_with_valid_phone_shows_all_numbers(
    logged_in_client,
    mock_get_service_template,
    mock_get_users_by_service,
    mock_get_detailed_service_for_today,
    service_one,
    fake_uuid,
    mock_s3_upload,
    mocker,
):

    mocker.patch(
        'app.main.views.send.s3download',
        return_value='\n'.join(['phone number'] + [
            '07700 9007{0:02d}'.format(final_two) for final_two in range(0, 53)
        ])
    )

    response = logged_in_client.post(
        url_for('main.send_messages', service_id=service_one['id'], template_id=fake_uuid),
        data={'file': (BytesIO(''.encode('utf-8')), 'valid.csv')},
        content_type='multipart/form-data',
        follow_redirects=True
    )
    with logged_in_client.session_transaction() as sess:
        assert sess['upload_data']['template_id'] == fake_uuid
        assert sess['upload_data']['original_file_name'] == 'valid.csv'
        assert sess['upload_data']['notification_count'] == 53

    content = response.get_data(as_text=True)
    assert response.status_code == 200
    assert '07700 900701' in content
    assert '07700 900749' in content
    assert '07700 900750' not in content
    assert 'Only showing the first 50 rows' in content

    mock_get_detailed_service_for_today.assert_called_once_with(service_one['id'])


@pytest.mark.parametrize('service_mock, should_allow_international', [
    (mock_get_service, False),
    (mock_get_international_service, True),
])
def test_upload_csvfile_with_international_validates(
    mocker,
    api_user_active,
    logged_in_client,
    mock_get_service_template,
    mock_s3_upload,
    mock_has_permissions,
    mock_get_users_by_service,
    mock_get_detailed_service_for_today,
    fake_uuid,
    service_mock,
    should_allow_international,
):

    service_mock(mocker, api_user_active)
    mocker.patch('app.main.views.send.s3download', return_value='')
    mock_recipients = mocker.patch(
        'app.main.views.send.RecipientCSV',
        return_value=RecipientCSV("", template_type="sms"),
    )

    response = logged_in_client.post(
        url_for('main.send_messages', service_id=fake_uuid, template_id=fake_uuid),
        data={'file': (BytesIO(''.encode('utf-8')), 'valid.csv')},
        content_type='multipart/form-data',
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert mock_recipients.call_args[1]['international_sms'] == should_allow_international


def test_test_message_can_only_be_sent_now(
    logged_in_client,
    mocker,
    service_one,
    mock_get_service_template,
    mock_s3_download,
    mock_get_users_by_service,
    mock_get_detailed_service_for_today,
    fake_uuid
):

    with logged_in_client.session_transaction() as session:
        session['upload_data'] = {
            'original_file_name': 'Test message',
            'template_id': fake_uuid,
            'notification_count': 1,
            'valid': True
        }
    response = logged_in_client.get(url_for(
        'main.check_messages',
        service_id=service_one['id'],
        upload_id=fake_uuid,
        template_type='sms',
        from_test=True
    ))

    content = response.get_data(as_text=True)
    assert 'name="scheduled_for"' not in content


def test_letter_can_only_be_sent_now(
    logged_in_client,
    mocker,
    service_one,
    mock_get_service_letter_template,
    mock_s3_download,
    mock_get_users_by_service,
    mock_get_detailed_service_for_today,
    fake_uuid,
):

    mocker.patch('app.main.views.send.get_page_count_for_letter', return_value=1)

    with logged_in_client.session_transaction() as session:
        session['upload_data'] = {
            'original_file_name': 'Test message',
            'template_id': fake_uuid,
            'notification_count': 1,
            'valid': True
        }

    response = logged_in_client.get(url_for(
        'main.check_messages',
        service_id=service_one['id'],
        upload_id=fake_uuid,
        template_type='letter',
        from_test=True
    ))

    content = response.get_data(as_text=True)
    assert 'name="scheduled_for"' not in content


@pytest.mark.parametrize('when', [
    '', '2016-08-25T13:04:21.767198'
])
def test_create_job_should_call_api(
    logged_in_client,
    service_one,
    mock_create_job,
    mock_get_job,
    mock_get_notifications,
    mock_get_service_template,
    mocker,
    fake_uuid,
    when
):
    service_id = service_one['id']
    data = mock_get_job(service_one['id'], fake_uuid)['job']
    job_id = data['job_id']
    original_file_name = data['original_file_name']
    template_id = data['template_id']
    notification_count = data['notification_count']
    with logged_in_client.session_transaction() as session:
        session['upload_data'] = {
            'original_file_name': original_file_name,
            'template_id': template_id,
            'notification_count': notification_count,
            'valid': True
        }
    url = url_for('main.start_job', service_id=service_one['id'], upload_id=job_id)
    response = logged_in_client.post(url, data={'scheduled_for': when}, follow_redirects=True)

    assert response.status_code == 200
    assert original_file_name in response.get_data(as_text=True)
    mock_create_job.assert_called_with(
        job_id,
        service_id,
        template_id,
        original_file_name,
        notification_count,
        scheduled_for=when
    )


def test_can_start_letters_job(
    logged_in_platform_admin_client,
    mock_create_job,
    service_one,
    fake_uuid
):

    with logged_in_platform_admin_client.session_transaction() as session:
        session['upload_data'] = {
            'original_file_name': 'example.csv',
            'template_id': fake_uuid,
            'notification_count': 123,
            'valid': True
        }
    response = logged_in_platform_admin_client.post(
        url_for('main.start_job', service_id=service_one['id'], upload_id=fake_uuid),
        data={}
    )
    assert response.status_code == 302


@pytest.mark.parametrize('filetype', ['pdf', 'png'])
def test_should_show_preview_letter_message(
    filetype,
    logged_in_platform_admin_client,
    mock_get_service_letter_template,
    mock_get_users_by_service,
    mock_get_detailed_service_for_today,
    service_one,
    fake_uuid,
    mocker,
):
    service_one['permissions'] = ['letter']
    mocker.patch('app.service_api_client.get_service', return_value={"data": service_one})
    mocker.patch('app.main.views.send.get_page_count_for_letter', return_value=1)

    mocker.patch(
        'app.main.views.send.s3download',
        return_value='\n'.join(
            ['address line 1, postcode'] +
            ['123 street, abc123']
        )
    )
    mocked_preview = mocker.patch(
        'app.main.views.send.TemplatePreview.from_utils_template',
        return_value='foo'
    )

    service_id = service_one['id']
    template_id = fake_uuid
    with logged_in_platform_admin_client.session_transaction() as session:
        session['upload_data'] = {
            'original_file_name': 'example.csv',
            'template_id': fake_uuid,
            'notification_count': 1,
            'valid': True
        }
    response = logged_in_platform_admin_client.get(
        url_for(
            'main.check_messages_preview',
            service_id=service_id,
            template_type='letter',
            upload_id=fake_uuid,
            filetype=filetype
        )
    )

    mock_get_service_letter_template.assert_called_with(service_id, template_id)

    assert response.status_code == 200
    assert response.get_data(as_text=True) == 'foo'
    assert mocked_preview.call_args[0][0].id == template_id
    assert type(mocked_preview.call_args[0][0]) == LetterPreviewTemplate
    assert mocked_preview.call_args[0][1] == filetype


def test_dont_show_preview_letter_templates_for_bad_filetype(
    logged_in_client,
    mock_get_service_template,
    service_one,
    fake_uuid
):
    resp = logged_in_client.get(
        url_for(
            'main.check_messages_preview',
            service_id=service_one['id'],
            template_type='letter',
            upload_id=fake_uuid,
            filetype='blah'
        )
    )
    assert resp.status_code == 404
    assert mock_get_service_template.called is False


def test_check_messages_should_revalidate_file_when_uploading_file(
    logged_in_client,
    service_one,
    mock_create_job,
    mock_get_job,
    mock_get_service_template_with_placeholders,
    mock_s3_upload,
    mocker,
    mock_get_detailed_service_for_today,
    mock_get_users_by_service,
    fake_uuid
):

    service_id = service_one['id']

    mocker.patch(
        'app.main.views.send.s3download',
        return_value="""
            phone number,name,,,
            +447700900986,,,,
            +447700900986,,,,
        """
    )
    data = mock_get_job(service_one['id'], fake_uuid)['job']
    with logged_in_client.session_transaction() as session:
        session['upload_data'] = {'original_file_name': 'invalid.csv',
                                  'template_id': data['template_id'],
                                  'notification_count': data['notification_count'],
                                  'valid': True}
    response = logged_in_client.post(
        url_for('main.start_job', service_id=service_one['id'], upload_id=data['job_id']),
        data={'file': (BytesIO(''.encode('utf-8')), 'invalid.csv')},
        content_type='multipart/form-data',
        follow_redirects=True
    )
    assert response.status_code == 200
    assert 'There is a problem with your data' in response.get_data(as_text=True)


@pytest.mark.parametrize('route, response_code', [
    ('main.choose_template', 200),
    ('main.send_messages', 200),
    ('main.get_example_csv', 200),
    ('main.send_test', 302)
])
def test_route_permissions(
    mocker,
    app_,
    client,
    api_user_active,
    service_one,
    mock_get_service_template,
    mock_get_service_templates,
    mock_get_jobs,
    mock_get_notifications,
    mock_create_job,
    mock_s3_upload,
    fake_uuid,
    route,
    response_code,
):
    validate_route_permission(
        mocker,
        app_,
        "GET",
        response_code,
        url_for(
            route,
            service_id=service_one['id'],
            template_id=fake_uuid
        ),
        ['send_texts', 'send_emails', 'send_letters'],
        api_user_active,
        service_one)


@pytest.mark.parametrize('route', [
    'main.choose_template',
    'main.send_messages',
    'main.get_example_csv',
    'main.send_test'
])
def test_route_invalid_permissions(
    mocker,
    app_,
    client,
    api_user_active,
    service_one,
    mock_get_service_template,
    mock_get_service_templates,
    mock_get_jobs,
    mock_get_notifications,
    mock_create_job,
    fake_uuid,
    route,
):
    validate_route_permission(
        mocker,
        app_,
        "GET",
        403,
        url_for(
            route,
            service_id=service_one['id'],
            template_type='sms',
            template_id=fake_uuid),
        ['blah'],
        api_user_active,
        service_one)


@pytest.mark.parametrize(
    'template_mock, extra_args, expected_url',
    [
        (
            mock_get_service_template,
            dict(),
            partial(url_for, '.send_messages')
        ),
        (
            mock_get_service_template_with_placeholders,
            dict(),
            partial(url_for, '.send_messages')
        ),
        (
            mock_get_service_template,
            dict(from_test=True),
            partial(url_for, '.view_template')
        ),
        (
            mock_get_service_letter_template,  # No placeholders
            dict(from_test=True),
            partial(url_for, '.send_test')
        ),
        (
            mock_get_service_template_with_placeholders,
            dict(from_test=True),
            partial(url_for, '.send_test')
        ),
        (
            mock_get_service_template_with_placeholders,
            dict(help='0', from_test=True),
            partial(url_for, '.send_test')
        ),
        (
            mock_get_service_template_with_placeholders,
            dict(help='2', from_test=True),
            partial(url_for, '.send_test', help='1')
        )
    ]
)
def test_check_messages_back_link(
    logged_in_client,
    api_user_active,
    mock_login,
    mock_get_user_by_email,
    mock_get_users_by_service,
    mock_get_service,
    mock_has_permissions,
    mock_get_detailed_service_for_today,
    mock_s3_download,
    fake_uuid,
    mocker,
    template_mock,
    extra_args,
    expected_url
):
    template_mock(mocker)
    with logged_in_client.session_transaction() as session:
        session['upload_data'] = {'original_file_name': 'valid.csv',
                                  'template_id': fake_uuid,
                                  'notification_count': 1,
                                  'valid': True}
    response = logged_in_client.get(url_for(
        'main.check_messages',
        service_id=fake_uuid,
        upload_id=fake_uuid,
        template_type='sms',
        **extra_args
    ))
    assert response.status_code == 200
    page = BeautifulSoup(response.data.decode('utf-8'), 'html.parser')
    assert (
        page.findAll('a', {'class': 'page-footer-back-link'})[0]['href']
    ) == expected_url(service_id=fake_uuid, template_id=fake_uuid)


def test_go_to_dashboard_after_tour(
    logged_in_client,
    mocker,
    api_user_active,
    mock_login,
    mock_get_service,
    mock_has_permissions,
    mock_delete_service_template,
    fake_uuid
):

    resp = logged_in_client.get(
        url_for('main.go_to_dashboard_after_tour', service_id=fake_uuid, example_template_id=fake_uuid)
    )

    assert resp.status_code == 302
    assert resp.location == url_for("main.service_dashboard", service_id=fake_uuid, _external=True)
    mock_delete_service_template.assert_called_once_with(fake_uuid, fake_uuid)


@pytest.mark.parametrize('num_requested,expected_msg', [
    (0, '‘valid.csv’ contains 100 phone numbers.'),
    (1, 'You can still send 49 messages today, but ‘valid.csv’ contains 100 phone numbers.')
], ids=['none_sent', 'some_sent'])
def test_check_messages_shows_too_many_messages_errors(
    mocker,
    logged_in_client,
    api_user_active,
    mock_login,
    mock_get_users_by_service,
    mock_get_service,
    mock_get_service_template,
    mock_has_permissions,
    fake_uuid,
    num_requested,
    expected_msg
):
    # csv with 100 phone numbers
    mocker.patch('app.main.views.send.s3download', return_value=',\n'.join(
        ['phone number'] + ([mock_get_users_by_service(None)[0]._mobile_number]*100)
    ))
    mocker.patch('app.service_api_client.get_detailed_service_for_today', return_value={
        'data': {
            'statistics': {
                'sms': {'requested': num_requested, 'delivered': 0, 'failed': 0},
                'email': {'requested': 0, 'delivered': 0, 'failed': 0}
            }
        }
    })

    with logged_in_client.session_transaction() as session:
        session['upload_data'] = {'original_file_name': 'valid.csv',
                                  'template_id': fake_uuid,
                                  'notification_count': 1,
                                  'valid': True}
    response = logged_in_client.get(url_for(
        'main.check_messages',
        service_id=fake_uuid,
        template_type='sms',
        upload_id=fake_uuid
    ))
    assert response.status_code == 200
    page = BeautifulSoup(response.data.decode('utf-8'), 'html.parser')
    assert page.find('h1').text.strip() == 'Too many recipients'
    assert page.find('div', class_='banner-dangerous').find('a').text.strip() == 'trial mode'

    # remove excess whitespace from element
    details = page.find('div', class_='banner-dangerous').findAll('p')[1]
    details = ' '.join([line.strip() for line in details.text.split('\n') if line.strip() != ''])
    assert details == expected_msg


def test_check_messages_shows_trial_mode_error(
    logged_in_client,
    mock_get_users_by_service,
    mock_get_service,
    mock_get_service_template,
    mock_has_permissions,
    mock_get_detailed_service_for_today,
    mocker
):
    mocker.patch('app.main.views.send.s3download', return_value=(
        'phone number,\n07900900321'  # Not in team
    ))
    with logged_in_client.session_transaction() as session:
        session['upload_data'] = {'template_id': ''}
    response = logged_in_client.get(url_for(
        'main.check_messages',
        service_id=uuid.uuid4(),
        template_type='sms',
        upload_id=uuid.uuid4()
    ))
    assert response.status_code == 200
    page = BeautifulSoup(response.data.decode('utf-8'), 'html.parser')
    assert ' '.join(
        page.find('div', class_='banner-dangerous').text.split()
    ) == (
        'You can’t send to this phone number '
        'In trial mode you can only send to yourself and members of your team '
        'Skip to file contents'
    )


def test_check_messages_shows_over_max_row_error(
    logged_in_client,
    api_user_active,
    mock_login,
    mock_get_users_by_service,
    mock_get_service,
    mock_get_service_template_with_placeholders,
    mock_has_permissions,
    mock_get_detailed_service_for_today,
    mock_s3_download,
    fake_uuid,
    mocker
):
    mock_recipients = mocker.patch('app.main.views.send.RecipientCSV').return_value
    mock_recipients.max_rows = 11111
    mock_recipients.__len__.return_value = 99999
    mock_recipients.too_many_rows.return_value = True

    with logged_in_client.session_transaction() as session:
        session['upload_data'] = {'template_id': fake_uuid}
    response = logged_in_client.get(url_for(
        'main.check_messages',
        service_id=fake_uuid,
        template_type='sms',
        upload_id=fake_uuid
    ))
    assert response.status_code == 200
    page = BeautifulSoup(response.data.decode('utf-8'), 'html.parser')
    assert ' '.join(
        page.find('div', class_='banner-dangerous').text.split()
    ) == (
        'Your file has too many rows '
        'Notify can process up to 11,111 rows at once. '
        'Your file has 99,999 rows. '
        'Skip to file contents'
    )


def test_non_ascii_characters_in_letter_recipients_file_shows_error(
    logged_in_client,
    api_user_active,
    mock_login,
    mock_get_users_by_service,
    mock_get_service,
    mock_has_permissions,
    mock_get_service_letter_template,
    mock_get_detailed_service_for_today,
    fake_uuid,
    mocker
):
    from tests.conftest import mock_s3_download
    mock_s3_download(
        mocker,
        content=u"""
        address line 1,address line 2,address line 3,address line 4,address line 5,address line 6,postcode
        Петя,345 Example Street,,,,,AA1 6BB
        """
    )

    with logged_in_client.session_transaction() as session:
        session['upload_data'] = {'template_id': fake_uuid}
    response = logged_in_client.get(url_for(
        'main.check_messages',
        service_id=fake_uuid,
        template_type='letter',
        upload_id=fake_uuid
    ))

    assert response.status_code == 200
    page = BeautifulSoup(response.data.decode('utf-8'), 'html.parser')
    assert ' '.join(
        page.find('div', class_='banner-dangerous').text.split()
    ) == (
            'There is a problem with your data '
            'You need to fix 1 address '
            'Skip to file contents'
        )
    assert page.find('span', class_='table-field-error-label').text == u'Can’t include П, е, т or я'


def test_check_messages_redirects_if_no_upload_data(logged_in_client, service_one, mocker):
    checker = mocker.patch('app.main.views.send.get_check_messages_back_url', return_value='foo')
    response = logged_in_client.get(url_for(
        'main.check_messages',
        service_id=service_one['id'],
        template_type='bar',
        upload_id='baz'
    ))

    checker.assert_called_once_with(service_one['id'], 'bar')
    assert response.status_code == 301
    assert response.location == 'http://localhost/foo'


@pytest.mark.parametrize('template_type', ['sms', 'email'])
def test_get_check_messages_back_url_returns_to_correct_select_template(client, mocker, template_type):
    mocker.patch('app.main.views.send.get_help_argument', return_value=False)

    assert get_check_messages_back_url('1234', template_type) == url_for(
        'main.choose_template',
        service_id='1234'
    )


def test_check_messages_back_from_help_goes_to_start_of_help(client, service_one, mocker):
    mocker.patch('app.main.views.send.get_help_argument', return_value=True)
    mocker.patch('app.service_api_client.get_service_templates', lambda service_id: {
        'data': [template_json(service_one['id'], '111', type_='sms')]
    })
    assert get_check_messages_back_url(service_one['id'], 'sms') == url_for(
        'main.send_test',
        service_id=service_one['id'],
        template_id='111',
        help='1'
    )


@pytest.mark.parametrize('templates', [
    [],
    [
        template_json('000', '111', type_='sms'),
        template_json('000', '222', type_='sms')
    ]
], ids=['no_templates', 'two_templates'])
def test_check_messages_back_from_help_handles_unexpected_templates(client, mocker, templates):
    mocker.patch('app.main.views.send.get_help_argument', return_value=True)
    mocker.patch('app.service_api_client.get_service_templates', lambda service_id: {
        'data': templates
    })

    assert get_check_messages_back_url('1234', 'sms') == url_for(
        'main.choose_template',
        service_id='1234',
    )
