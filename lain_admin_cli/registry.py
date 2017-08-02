# -*- coding: utf-8 -*-
import operator
import requests
import httplib
import json
import time

from datetime import datetime
from os import environ
from argh.decorators import arg
from argh import CommandError
from subprocess import check_output
from lain_admin_cli.helpers import (
    TwoLevelCommandBase, info, error
)

REPOS_URL_TEMPLATE = "http://%s/v2/_catalog"
TAGS_URL_TEMPLATE = "http://%s/v2/%s/tags/list"
MANIFEST_URL_TEMPLATE = "http://%s/v2/%s/manifests/%s"

REPOSITORIES = "repositories"
PREPARE = "prepare"
META = "meta"
RELEASE = "release"

registry_host = "registry.lain.local"
REGISTRY_FORMAT = "registry.%s"

TOKEN_CACHE = {}
REALM = "Bearer realm"
SCOPE = "scope"
SERVICE = "service"

TIME_OUT = environ.get('REGISTRY_TIMEOUT', 5)

DEFAULT_REMAIN_TIME = environ.get('REMAIN_TIME', 30 * 24 * 3600)


def _time_during(ts1, ts2, t):
    total_seconds = (datetime.fromtimestamp(ts1) -
                     datetime.fromtimestamp(ts2)).total_seconds()
    return total_seconds <= t


def _domain():
    try:
        return check_output(['etcdctl', 'get', '/lain/config/domain']).strip('\n')
    except Exception as e:
        error('Get lain domain failed! error:%s', str(e))


def _request_auth(session, method, url, auth_head, **kwargs):
    token = _token(auth_head)
    if token is None:
        return
    headers = {'Authorization': 'Bearer %s' % token}
    headers.update(kwargs)
    try:
        resp = session.request(method, url, headers=headers, timeout=TIME_OUT)
        if resp.status_code >= 300 or resp.status_code < 200:
            error('Requests url(%s) failed! error: registry server faltal error', url)
            return
        if resp.status_code == 401:
            token = _token(auth_head, expired=True)
            if token is None:
                return resp
            headers['Authorization'] = 'Bearer %s' % token
            resp = session.request(
                method, url, headers=headers, timeout=TIME_OUT)
        return resp
    except Exception as e:
        error('Requests url(%s) failed! error:%s', url, str(e))


def _request(session, method, url, **kwargs):
    try:
        resp = session.request(method, url, headers=kwargs, timeout=TIME_OUT)
        if resp.status_code == 401:
            auth_head = resp.headers['Www-Authenticate']
            resp_auth = _request_auth(
                session, method, url, auth_head, **kwargs)
            if resp_auth is not None:
                resp = resp_auth
        if resp is None:
            return
        if resp.status_code >= 300 or resp.status_code < 200:
            error('Requests url(%s) failed! error: registry server faltal error', url)
            return
        return resp
    except Exception as e:
        error('Requests url(%s) failed! error:%s', url, str(e))


def _token(auth_head, expired=False):
    token_url = _token_url(auth_head)
    token = TOKEN_CACHE.get(token_url)
    if not expired and token is not None:
        return token
    try:
        resp = requests.get(token_url)
        token = resp.json().get('token')
    except Exception as e:
        error('Fetch auth token failed ! error:%s', str(e))
        return token
    if token is not None:
        TOKEN_CACHE[token_url] = token
    return token


def _token_url(auth_head):
    auth_infos = auth_head.split(',')
    token_params = {}
    for info in auth_infos:
        k, v = info.split('=')
        token_params[k] = v.strip('"')
    token_url = "%s?service=%s&scope=%s" % (token_params[REALM], token_params[
                                            SERVICE], token_params[SCOPE])
    return token_url


class Repo:

    def __init__(self, repo_name, tags):
        self.repo_name = repo_name
        self.tags = tags


class Image:

    def __init__(self, repo_name, tag, digest):
        self.repo_name = repo_name
        self.tag = tag
        self.digest = digest

    def __str__(self):
        return "%s, %s, %s" % (self.repo_name, self.tag, self.digest)


def _repos_in_registry(session):
    repo_url = REPOS_URL_TEMPLATE % registry_host
    resp = _request(session, 'GET', repo_url)
    if resp is None:
        return[]
    try:
        return resp.json().get(REPOSITORIES)
    except Exception as e:
        error('Fetch all repositories failed! error:%s', str(e))
    return []


def _digest_from_tag(session, repo, tag):
    manifest_url = MANIFEST_URL_TEMPLATE % (registry_host, repo, tag)
    resp = _request(session, 'HEAD', manifest_url,
                    Accept="application/vnd.docker.distribution.manifest.v2+json")
    if resp is None:
        return ""
    return resp.headers.get('Docker-Content-Digest')


def _images_in_repo(session, repo):
    tags_url = TAGS_URL_TEMPLATE % (registry_host, repo)
    resp = _request(session, 'GET', tags_url)
    if resp is None:
        return []
    try:
        images = []
        tags = resp.json().get('tags')
        if not tags:
            return images
        for tag in tags:
            digest = _digest_from_tag(session, repo, tag)
            if digest != "":
                images.append(Image(repo, tag, digest))
        return images
    except Exception as e:
        error('Fetch repo(%s)\'s images failed! error:%s', repo, str(e))
    return []


def _image_delete(session, image):
    manifest_url = MANIFEST_URL_TEMPLATE % (
        registry_host, image.repo_name, image.digest)
    resp = _request(session, 'DELETE', manifest_url)
    info("Delete image(%s) result:%s ", image, resp)


def expired_repo_clear(session, repo, repo_remain, time_remain):
    info('----------------------------')
    info('Start clean registry repo %s', repo)
    try:
        image_tag_split_len = 3
        pos_timestamp = 1
        images = _images_in_repo(session, repo)
        if len(images) <= repo_remain:
            return
        meta_images_map = {}
        rels_images_map = {}
        prep_images_map = {}
        now = time.time()
        for image in images:
            tags_info = image.tag.split('-')
            if len(tags_info) != image_tag_split_len:
                continue

            try:
                if image.tag.startswith(PREPARE):
                    timestamp = int(tags_info[pos_timestamp + 1])
                else:
                    timestamp = int(tags_info[pos_timestamp])
            except Exception as e:
                if image.tag.find('-config-') > 0:
                    info("Deleting special config image: %s", image)
                    _image_delete(session, image)
                continue
            if(_time_during(now, timestamp, time_remain)):
                continue
            if image.tag.startswith(META):
                meta_images_map[timestamp] = image
            elif image.tag.startswith(RELEASE):
                rels_images_map[timestamp] = image
            elif image.tag.startswith(PREPARE):
                prep_images_map[timestamp] = image

        prep_images = sort_map_values(prep_images_map)
        meta_images = sort_map_values(meta_images_map)
        rels_images = sort_map_values(rels_images_map)

        for image in prep_images[repo_remain:]:
            info("Deleting image: %s", image)
            _image_delete(session, image)
        for image in meta_images[repo_remain:]:
            info("Deleting image: %s", image)
            _image_delete(session, image)
        for image in rels_images[repo_remain:]:
            info("Deleting image: %s", image)
            _image_delete(session, image)
    except Exception as e:
        error('Clean registry failed! error:%s', str(e))
    finally:
        info('Clean registry repo %s over', repo)


def expired_all_repos_clear(session, repo_remain, time_remain):
    info('Start clean registry')
    info('============================')
    repos = _repos_in_registry(session)
    if not isinstance(repos, list) or len(repos) == 0:
        return
    for repo in repos:
        expired_repo_clear(session, repo, repo_remain, time_remain)
    info('============================')
    info('Clean registry over')


def sort_map_values(origin_map):
    return [item[1] for item in sorted(origin_map.items(),
                                       key=operator.itemgetter(0), reverse=True)]


class Registry(TwoLevelCommandBase):

    @classmethod
    def subcommands(self):
        return [self.list, self.clean]

    @classmethod
    def namespace(self):
        return "registry"

    @classmethod
    def help_message(self):
        return "lain registry operations"

    @classmethod
    @arg('-t', '--target', required=False, help="target repository in registry")
    def list(self, target="all"):
        session = requests.Session()
        self._update_domain()
        if target == "all":
            repos = _repos_in_registry(session)
            for repo in repos:
                info(repo)
        else:
            images = _images_in_repo(session, target)
            for image in images:
                info('%s', image)

    @classmethod
    @arg('-t', '--target', required=False, help="clean target repository in registry")
    @arg('-n', '--num', required=False, help="repository's remained quantity of images in registry(must bigger than 0)")
    @arg('-d', '--time', required=False, help="repository's remained time(seconds) of images in registry(must bigger than 0)")
    def clean(self, num=20, time=DEFAULT_REMAIN_TIME, target="all"):
        session = requests.Session()
        self._update_domain()
        if num < 1:
            raise CommandError("num must bigger than 0")
        if time < 1:
            raise CommandError("time must bigger than 0")
        if target == "all":
            expired_all_repos_clear(session, num, time)
        else:
            expired_repo_clear(session, target, num, time)

    @classmethod
    def _update_domain(self):
        domain = _domain()
        if domain is not None:
            global registry_host
            registry_host = REGISTRY_FORMAT % _domain()
