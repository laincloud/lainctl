# -*- coding: utf-8 -*-

import getpass
import requests
from subprocess import check_output, check_call, CalledProcessError, STDOUT
from abc import ABCMeta, abstractmethod
import os, json
from urlparse import urlparse, parse_qs
from urllib import urlencode

requests.packages.urllib3.disable_warnings()
playbooks_path = ""
volume_dir = "/data/lain/volumes"
rsync_secrets_file = "/etc/rsyncd.secrets"
logs_dir = "/lain/logs"


class TwoLevelCommandBase(object):
    __metaclass__ = ABCMeta

    @abstractmethod
    def subcommands(self):
        '''return subcommand function list'''

    @abstractmethod
    def namespace(self):
        '''return namespace string'''

    @abstractmethod
    def help_message(self):
        '''return help message string'''


class RemoveException(Exception):
    pass


class AddNodeException(Exception):
    pass


class Node(object):
    name = ""
    ip = ""
    ssh_port = 22
    docker_device = ""
    is_lain_manager = False
    is_etcd_member = False
    is_swarm_manager = False

    def __init__(self, nodename=""):
        if nodename == "":
            return
        output = check_output(['etcdctl', 'ls', '/lain/nodes/nodes'])

        for line in output.splitlines():
            if nodename == line.split('/')[-1].split(':')[0]:
                output = check_output(['etcdctl', 'get', line]).strip()
                dic = json.loads(output)
                self.name = nodename
                self.ip = dic['ip']
                self.ssh_port = int(dic['ssh_port'])
                self.docker_device = dic['docker_device']
                return
        raise(Exception("unkown nodename %s" % nodename))


class Container(object):
    name = ""
    appname = ""
    proctype = ""
    procname = ""
    podname = ""
    instance = 1
    version = 0
    drift = 0
    info = {}
    volumes = []
    host = ""

    def __init__(self, name):
        try:
            output = check_output(['docker', '-H', 'swarm.lain:2376',
                                   'inspect', "%s" % (name)])
            self.info = json.loads(output)[0]
        except CalledProcessError as e:
            error("Fail to inspect container %s" % (name))
            raise(e)
        for env in self.info['Config']['Env']:
            fields = env.split('=')
            if  fields[0] == 'LAIN_APPNAME':
                self.appname = fields[1]
            elif fields[0] == 'LAIN_PROCNAME':
                self.procname = fields[1]
            elif fields[0] == 'DEPLOYD_POD_NAME':
                self.podname = fields[1]
            elif fields[0] == 'DEPLOYD_POD_INSTANCE_NO':
                self.instance = int(fields[1])
        self.proctype = self.podname.split('.')[-2]
        fields = self.info['Name'].split('.')[-1].split('-')
        self.version = int(fields[0][1:])
        self.drift = int(fields[2][1:])
        self.name = name
        self.host = self.info['Node']['Name']

        for v in self.info['Mounts']:
            # logs_dir can be discarded when drift
            if v['Source'].find('/data/lain/volumes') >= 0 and v['Destination'] != logs_dir:
                self.volumes.append(v['Source'])


class SSOAccess(object):
    """access to sso for SSOAccess"""

    client_id = ''
    client_secret = ''
    redirect_uri = ''
    grant_type = 'authorization_code'
    scope = 'write:group'
    auth_url = ''
    sso_url = ''
    authorization_endpoint = ''
    token_endpoint = ''

    def __init__(self, sso_url, client_id, client_secret, redirect_uri):
        SSOAccess.sso_url = sso_url
        SSOAccess.client_id = client_id
        SSOAccess.client_secret = client_secret
        SSOAccess.redirect_uri = redirect_uri
        SSOAccess.authorization_endpoint = SSOAccess.sso_url + '/oauth2/auth'
        SSOAccess.token_endpoint = SSOAccess.sso_url + '/oauth2/token'
        SSOAccess.auth_url = SSOAccess.authorization_endpoint + '?' + urlencode({
            'client_id': client_id,
            'response_type': 'code',
            'scope': SSOAccess.scope,
            'redirect_uri': redirect_uri,
            'state': 'foobar',
        })

    @classmethod
    def new(cls, sso_url=None, cid=None, secret=None, redirect_uri=None):
        if sso_url is None:
            sso_url = 'https://sso.lain.local'
        if cid is None:
            cid = 3
        if secret is None:
            secret = 'lain-cli_admin'
        if redirect_uri is None:
            redirect_uri = 'https://example.com'

        return SSOAccess(sso_url, cid, secret, redirect_uri)

    def get_auth_code(self, username, password):
        try:
            usr_msg = {'login': username, 'password': password}
            result = requests.post(
                self.auth_url,
                data=usr_msg,
                allow_redirects=False)
            code_callback_url = result.headers['Location']
            authentication = parse_qs(urlparse(code_callback_url).query)
            return True, authentication['code'][0]
        except Exception:
            warn("Please insure '%s' is accessable.", self.auth_url)
            warn("If not, please specify the sso cid and secret when login.")
            return False, ''

    def get_auth_token(self, code):
        auth_msg = {
            'client_id': self.client_id,
            'grant_type': 'authorization_code',
            'client_secret': self.client_secret,
            'code': code,
            'redirect_uri': self.redirect_uri
        }

        try:
            result = requests.request(
                "GET", self.token_endpoint,
                headers=None,
                params=auth_msg)
        except requests.HTTPError:
            return False, '', ''

        accessinfo = result.json()
        return True, accessinfo['access_token'], accessinfo['refresh_token']


def sso_login(sso_url, cid, secret, redirect_uri):
    username = raw_input('SSO Username:')
    password = getpass.getpass('SSO Password:')
    sso_access = SSOAccess.new(sso_url, cid, secret, redirect_uri)
    get_code_success, code = sso_access.get_auth_code(username, password)
    if not get_code_success:
        warn('Login failed, get_auth_code failed')
        return False, ''
    get_token_success, access_token, refresh_token = sso_access.get_auth_token(code)
    if not get_token_success:
        warn('Login failed, get_auth_token failed')
        return False, ''
    return True, access_token


def run_ansible_cmd(playbooks_path, envs, file_name='role.yaml'):
    cmd = ['ansible-playbook', '-i', os.path.join(playbooks_path, 'cluster')]
    for k, v in envs.iteritems():
        cmd += ['-e', '%s=%s' % (k, v)]
    cmd += [os.path.join(playbooks_path, file_name)]
    info('cmd is: %s', ' '.join(cmd))
    try:
        check_call(cmd)
    except CalledProcessError:
        error("ansible-playbook failed to run.")
        error("If you see some nodes unreachable, try run this commond first and retry:\n"
              "      sudo ssh-copy-id -i /root/.ssh/lain.pub root@NODE_IP\n"
              "    (replace NODE_IP with failed node's IP)")
        return 1

def get_rsyncd_secrets():
    with open(rsync_secrets_file) as f:
        secrets = f.read().split(':')[-1]
    return secrets

def get_domain():
    return check_output(['etcdctl', 'get', '/lain/config/domain']).strip()

def is_backupd_enabled():
    try:
        output = check_output(['etcdctl', 'get', '/lain/config/backup_enabled'], stderr=STDOUT).strip()
    except CalledProcessError:
        return False
    else:
        return output == 'true'


def info(pattern, *args):
    print(_green(">>> " + pattern % args))

def error(pattern, *args):
    print(_red(">>> " + pattern % args, True))

def warn(pattern, *args):
    print(_yellow(">>> " + pattern % args, True))

def yes_or_no(prompt, default='yes', color=None):
    if default not in ('yes', 'no'):
        raise Exception("default must be either yes or no")
    question = '(Y/n)' if default == 'yes' else '(y/N)'
    text = '%s %s ' % (prompt, question)
    if color:
        text = color(text)
    while True:
        answer = raw_input(text)
        if not answer:
            return default == 'yes'
        if answer.lower() in ('y', 'yes'):
            return True
        elif answer.lower() in ('n', 'no'):
            return False
        print("Please input yes or no")

def _colorize(code):
    def _(text, bold=False):
        c = code
        if bold:
            c = '1;%s' % c
        return '\033[%sm%s\033[0m' % (c, text)
    return _

_red = _colorize('31')
_green = _colorize('32')
_yellow = _colorize('33')

