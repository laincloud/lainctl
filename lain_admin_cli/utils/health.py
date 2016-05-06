# -*- coding: utf-8 -*-
import requests
from lain_admin_cli.helpers import info, error, warn
from subprocess import check_call, call, Popen, PIPE


class ClusterHealth(object):

    CHECK_LIST = ['etcd', 'swarm', 'deployd', 'console']

    def run(self):
        for item in self.CHECK_LIST:
            if self.check(item):
                info("%s is ok" % item)
            else:
                error("%s is not ok" % item)

    def check(self, item):
        try:
            return getattr(self, "check_%s" % item)()
        except:
            return False

    def check_etcd(self):
        url = "http://etcd.lain:4001/health"
        resp = requests.get(url, timeout=5)
        data = resp.json()
        return data.get('health')

    def check_console(self):
        url = "http://console.lain/"
        resp = requests.get(url, timeout=5)
        return resp.status_code == 200

    def check_deployd(self):
        url = "http://deployd.lain:9003/api/status"
        resp = requests.get(url, timeout=5)
        data = resp.json()
        return 'status' in data

    def check_swarm(self):
        url = "http://swarm.lain:2376/_ping"
        resp = requests.get(url, timeout=5)
        return resp.status_code == 200


class NodeHealth(object):

    CHECK_LIST = ['dnsmasq', 'etcd', 'docker', 'swarm_agent', 'lainlet', 'networkd']

    def run(self):
        # TODO(xutao) check enabled feature
        for item in self.CHECK_LIST:
            if self.check(item):
                info("%s is ok" % item)
            else:
                error("%s is not ok" % item)

    def check(self, item):
        try:
            return getattr(self, "check_%s" % item)()
        except:
            return False

    def check_dnsmasq(self):
        return check_systemd('dnsmasq')

    def check_etcd(self):
        url = "http://etcd.lain:4001/health"
        resp = requests.get(url, timeout=5)
        data = resp.json()
        return data.get('health')

    def check_docker(self):
        url = "http://lainlet.lain:2375/_ping"
        resp = requests.get(url, timeout=5)
        return resp.status_code == 200

    def check_swarm_agent(self):
        return check_systemd('swarm-agent.service')

    def check_lainlet(self):
        return check_systemd('lainlet.service')

    def check_networkd(self):
        return check_systemd('networkd.service')


def check_systemd(service):
    p = Popen(['systemctl', 'show', service], stdout=PIPE, stderr=PIPE)
    output, err = p.communicate()
    if p.returncode != 0:
        return False
    for line in output.split('\n'):
        if line.startswith('ActiveState=active'):
            return True
    return False
