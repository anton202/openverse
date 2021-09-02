"""
End-to-end API tests. Can be used to verify a live deployment is functioning as
designed. Run with the `pytest -s` command from this directory.
"""

import requests
import json
import pytest
import uuid
import time
import catalog.settings
from django.db.models import Max
from django.urls import reverse

from catalog.api.licenses import LICENSE_GROUPS
from catalog.api.models import Image, OAuth2Verification
from catalog.api.utils.watermark import watermark

from test.constants import API_URL


@pytest.fixture
def image_fixture():
    response = requests.get(f'{API_URL}/v1/images?q=dog', verify=False)
    assert response.status_code == 200
    parsed = json.loads(response.text)
    return parsed


def test_link_shortener_create():
    payload = {'full_url': 'abcd'}
    response = requests.post(f'{API_URL}/v1/link/', json=payload, verify=False)
    assert response.status_code == 410


def test_link_shortener_resolve():
    response = requests.get(f'{API_URL}/v1/link/abc', verify=False)
    assert response.status_code == 410


@pytest.mark.skip(reason="Disabled feature")
@pytest.fixture
def test_list_create(image_fixture):
    payload = {
        'title': 'INTEGRATION TEST',
        'images': [image_fixture['results'][0]['id']]
    }
    response = requests.post(f'{API_URL}/list', json=payload, verify=False)
    parsed_response = json.loads(response.text)
    assert response.status_code == 201
    return parsed_response


@pytest.mark.skip(reason="Disabled feature")
def test_list_detail(test_list_create):
    list_slug = test_list_create['url'].split('/')[-1]
    response = requests.get(
        f'{API_URL}/list/{list_slug}', verify=False
    )
    assert response.status_code == 200


@pytest.mark.skip(reason="Disabled feature")
def test_list_delete(test_list_create):
    list_slug = test_list_create['url'].split('/')[-1]
    token = test_list_create['auth']
    headers = {"Authorization": f"Token {token}"}
    response = requests.delete(
        f'{API_URL}/list/{list_slug}',
        headers=headers,
        verify=False
    )
    assert response.status_code == 204


def test_license_type_filtering():
    """
    Ensure that multiple license type filters interact together correctly.
    """
    commercial = LICENSE_GROUPS['commercial']
    modification = LICENSE_GROUPS['modification']
    commercial_and_modification = set.intersection(modification, commercial)
    response = requests.get(
        f'{API_URL}/v1/images?q=dog&license_type=commercial,modification',
        verify=False
    )
    parsed = json.loads(response.text)
    for result in parsed['results']:
        assert result['license'].upper() in commercial_and_modification


def test_single_license_type_filtering():
    commercial = LICENSE_GROUPS['commercial']
    response = requests.get(
        f'{API_URL}/v1/images?q=dog&license_type=commercial', verify=False
    )
    parsed = json.loads(response.text)
    for result in parsed['results']:
        assert result['license'].upper() in commercial


def test_specific_license_filter():
    response = requests.get(
        f'{API_URL}/v1/images?q=dog&license=by', verify=False
    )
    parsed = json.loads(response.text)
    for result in parsed['results']:
        assert result['license'] == 'by'


def test_creator_quotation_grouping():
    """
    Users should be able to group terms together with quotation marks to narrow
    down their searches more effectively.
    """
    no_quotes = json.loads(
        requests.get(
            f'{API_URL}/v1/images?creator=william%20ford%stanley',
            verify=False
        ).text
    )
    quotes = json.loads(
        requests.get(
            f'{API_URL}/v1/images?creator="william%20ford%stanley"',
            verify=False
        ).text
    )
    # Did quotation marks actually narrow down the search?
    assert len(no_quotes['results']) > len(quotes['results'])
    # Did we find only William Ford Stanley works, or also by others?
    for result in quotes['results']:
        assert 'William Ford Stanley' in result['creator']


@pytest.fixture
def test_auth_tokens_registration():
    payload = {
        'name': f'INTEGRATION TEST APPLICATION {uuid.uuid4()}',
        'description': 'A key for testing the OAuth2 registration process.',
        'email': 'example@example.org'
    }
    response = requests.post(
        f'{API_URL}/v1/auth_tokens/register', json=payload, verify=False
    )
    parsed_response = json.loads(response.text)
    assert response.status_code == 201
    return parsed_response


@pytest.fixture
def test_auth_token_exchange(test_auth_tokens_registration):
    client_id = test_auth_tokens_registration['client_id']
    client_secret = test_auth_tokens_registration['client_secret']
    token_exchange_request = f'client_id={client_id}&'\
                             f'client_secret={client_secret}&'\
                             'grant_type=client_credentials'
    headers = {
        'content-type': "application/x-www-form-urlencoded",
        'cache-control': "no-cache",
    }
    response = json.loads(
        requests.post(
            f'{API_URL}/v1/auth_tokens/token/',
            data=token_exchange_request,
            headers=headers,
            verify=False
        ).text
    )
    assert 'access_token' in response
    return response


def test_auth_rate_limit_reporting(test_auth_token_exchange, verified=False):
    # We're anonymous still, so we need to wait a second before exchanging
    # the token.
    time.sleep(1)
    token = test_auth_token_exchange['access_token']
    headers = {
        'Authorization': f'Bearer {token}'
    }
    response = json.loads(
        requests.get(f'{API_URL}/v1/rate_limit', headers=headers).text
    )
    if verified:
        assert response['rate_limit_model'] == 'standard'
        assert response['verified'] is True
    else:
        assert response['rate_limit_model'] == 'standard'
        assert response['verified'] is False


@pytest.fixture(scope='session')
def django_db_setup():
    if API_URL == 'http://localhost:8000':
        catalog.settings.DATABASES['default'] = {
            'ENGINE': 'django.db.backends.postgresql',
            'HOST': '127.0.0.1',
            'NAME': 'openledger',
            'PASSWORD': 'deploy',
            'USER': 'deploy',
            'PORT': 5432
        }


@pytest.mark.django_db
def test_auth_email_verification(test_auth_token_exchange, django_db_setup):
    # This test needs to cheat by looking in the database, so it will be
    # skipped in non-local environments.
    if API_URL == 'http://localhost:8000':
        _id = OAuth2Verification.objects.aggregate(Max('id'))['id__max']
        verify = OAuth2Verification.objects.get(id=_id)
        code = verify.code
        path = reverse('verify-email', args=[code])
        url = f'{API_URL}{path}'
        response = requests.get(url)
        assert response.status_code == 200
        test_auth_rate_limit_reporting(
            test_auth_token_exchange, verified=True
        )


@pytest.mark.skip(reason="Unmaintained feature/grequests ssl recursion bug")
def test_watermark_preserves_exif():
    img_with_exif = 'https://raw.githubusercontent.com/ianare/exif-samples/' \
                    'master/jpg/Canon_PowerShot_S40.jpg'
    info = {
        'title': 'test',
        'creator': 'test',
        'license': 'test',
        'license_version': 'test'
    }
    _, exif = watermark(image_url=img_with_exif, info=info)
    assert exif is not None

    img_no_exif = 'https://creativecommons.org/wp-content/uploads/' \
                  '2019/03/9467312978_64cd5d2f3b_z.jpg'
    _, no_exif = watermark(image_url=img_no_exif, info=info)
    assert no_exif is None


def test_attribution():
    """
    The API includes an attribution string. Since there are some works where
    the title or creator is not known, the format of the attribution string
    can need to be tweaked slightly.
    """
    title_and_creator_missing = Image(
        identifier="ab80dbe1-414c-4ee8-9543-f9599312aeb8",
        title=None,
        creator=None,
        license="by",
        license_version="3.0"
    )
    assert "This work" in title_and_creator_missing.attribution

    title = "A foo walks into a bar"
    creator_missing = Image(
        identifier="ab80dbe1-414c-4ee8-9543-f9599312aeb8",
        title=title,
        creator=None,
        license="by",
        license_version="3.0"
    )
    assert title in creator_missing.attribution
    assert "by " not in creator_missing.attribution

    creator = "John Doe"
    title_missing = Image(
        identifier="ab80dbe1-414c-4ee8-9543-f9599312aeb8",
        title=None,
        creator=creator,
        license="by",
        license_version="3.0"
    )
    assert creator in title_missing.attribution
    assert "This work" in title_missing.attribution

    all_data_present = Image(
        identifier="ab80dbe1-414c-4ee8-9543-f9599312aeb8",
        title=title,
        creator=creator,
        license="by",
        license_version="3.0"
    )
    assert title in all_data_present.attribution
    assert creator in all_data_present.attribution


def test_license_override():
    null_license_url = Image(
        identifier="ab80dbe1-414c-4ee8-9543-f9599312aeb8",
        title="test",
        creator="test",
        license="by",
        license_version="3.0",
        meta_data={'license_url': 'null'}
    )
    assert null_license_url.license_url is not None


def test_source_search():
    response = requests.get(
        f'{API_URL}/v1/images?source=flickr', verify=False
    )
    if response.status_code != 200:
        print(f'Request failed. Message: {response.body}')
    assert response.status_code == 200
    parsed = json.loads(response.text)
    assert parsed['result_count'] > 0


def test_extension_filter():
    response = requests.get(f'{API_URL}/v1/images?q=dog&extension=jpg')
    parsed = json.loads(response.text)
    for result in parsed['results']:
        assert '.jpg' in result['url']


@pytest.fixture
def search_factory():
    """
    Allows passing url parameters along with a search request.
    """

    def _parameterized_search(**kwargs):
        response = requests.get(
            f'{API_URL}/v1/images',
            params=kwargs,
            verify=False
        )
        assert response.status_code == 200
        parsed = response.json()
        return parsed
    return _parameterized_search


@pytest.fixture
def search_with_dead_links(search_factory):
    """
    Here we pass filter_dead = False.
    """
    def _search_with_dead_links(**kwargs):
        return search_factory(filter_dead=False, **kwargs)
    return _search_with_dead_links


@pytest.fixture
def search_without_dead_links(search_factory):
    """
    Here we pass filter_dead = True.
    """
    def _search_without_dead_links(**kwargs):
        return search_factory(filter_dead=True, **kwargs)
    return _search_without_dead_links


def test_page_size_removing_dead_links(search_without_dead_links):
    """
    We have about 500 dead links in the sample data and should have around
    8 dead links in the first 100 results on a query composed of a single
    wildcard operator.

    Test whether the number of results returned is equal to the requested
    page_size of 100.
    """
    data = search_without_dead_links(q='*', page_size=100)
    assert len(data['results']) == 100


def test_dead_links_are_correctly_filtered(search_with_dead_links,
                                           search_without_dead_links):
    """
    Test the results for the same query with and without dead links are
    actually different.

    We use the results' id to compare them.
    """
    data_with_dead_links = search_with_dead_links(q='*', page_size=100)
    data_without_dead_links = search_without_dead_links(q='*', page_size=100)

    comparisons = []
    for result_1 in data_with_dead_links['results']:
        for result_2 in data_without_dead_links['results']:
            comparisons.append(result_1['id'] == result_2['id'])

    # Some results should be different
    # so we should have less than 100 True comparisons
    assert comparisons.count(True) < 100


def test_page_consistency_removing_dead_links(search_without_dead_links):
    """
    Test the results returned in consecutive pages are never repeated when
    filtering out dead links.
    """
    total_pages = 30
    page_size = 5

    page_results = []
    for page in range(1, total_pages + 1):
        page_data = search_without_dead_links(
            q='*',
            page_size=page_size,
            page=page
        )
        page_results += page_data['results']

    def no_duplicates(l):
        s = set()
        for x in l:
            if x in s:
                return False
            s.add(x)
        return True

    ids = list(map(lambda x: x['id'], page_results))
    # No results should be repeated so we should have no duplicate ids
    assert no_duplicates(ids)


@pytest.fixture
def recommendation_factory():
    """
    Allows passing url parameters along with a related images request.
    """

    def _parameterized_search(identifier, **kwargs):
        response = requests.get(
            f'{API_URL}/v1/recommendations?type=images&id={identifier}',
            params=kwargs,
            verify=False
        )
        assert response.status_code == 200
        parsed = response.json()
        return parsed

    return _parameterized_search


@pytest.mark.skip(reason="Generally, we don't paginate related images, so "
                         "consistency is less of an issue.")
def test_related_image_search_page_consistency(
        recommendation, search_without_dead_links
):
    initial_images = search_without_dead_links(q='*', page_size=10)
    for image in initial_images['results']:
        related = recommendation_factory(image['id'])
        assert related['result_count'] > 0
        assert len(related['results']) == 10
