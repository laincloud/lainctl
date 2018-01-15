# -*- coding: utf-8 -*-

from argh import CommandError
from argh.decorators import arg, expects_obj
from lain_admin_cli.helpers import Node as NodeInfo
from lain_admin_cli.helpers import (
    yes_or_no, info, warn, error, RemoveException, AddNodeException, _yellow,
    TwoLevelCommandBase, run_ansible_cmd
)
from subprocess import check_output, check_call, Popen, STDOUT, PIPE
import requests
import signal
import json
import os
import sys
import time
from lain_admin_cli.utils.health import NodeHealth


def sigint_handler(signum, frame):
    pass

signal.signal(signal.SIGTERM, sigint_handler)
signal.signal(signal.SIGINT, sigint_handler)


class Node(TwoLevelCommandBase):

    @classmethod
    def subcommands(self):
        return [self.list, self.inspect, self.add, self.remove, self.clean,
                self.maintain, self.health, self.change_labels]

    @classmethod
    def namespace(self):
        return "node"

    @classmethod
    def help_message(self):
        return "lain node operations"

    @classmethod
    def __list_node_group(self, group):
        output = check_output(['etcdctl', 'ls', '/lain/nodes/%s' % group])
        nodes = {}
        for line in output.splitlines():
            tmp = NodeInfo()
            tmp.name, tmp.ip, tmp.ssh_port = line.split('/')[-1].split(':')
            nodes[tmp.name] = tmp
        return nodes

    @classmethod
    def list(self):
        """list all the nodes(name and ip) in lain"""
        check_output(['etcdctl', 'ls', '/lain/nodes/nodes'])
        nodes = self.__list_node_group('nodes')

        # The column margin is 2 spaces
        min_width = 2 + max(8, *(len(node.name) for node in nodes.values()))
        row_fmt = "%-{min_width}s%s".format(min_width=min_width)
        print row_fmt % ("NODENAME", "IP")
        for node in nodes.values():
            print row_fmt % (node.name, node.ip)

    @classmethod
    @arg('node')
    def inspect(self, node):
        """
        inspect a node, nodename or nodeip should be given.
        info is got from etcd.
        """
        check_output(['etcdctl', 'ls', '/lain/nodes/nodes'])
        all_nodes = self.__list_node_group('nodes')
        for item in all_nodes.values():
            if node == item.name or node == item.ip:
                etcd_members = self.__list_node_group('etcd-members')
                swarm_members = self.__list_node_group('swarm-managers')
                managers = self.__list_node_group('managers')
                print json.dumps({
                    "name": item.name,
                    "ip": item.ip,
                    "ssh_port": item.ssh_port,
                    "docker_device": item.docker_device,
                    "is_lain_managers": node in managers,
                    "is_etcd_member": node in etcd_members,
                    "is_swarm_manager": node in swarm_members,
                    "labels": self.__get_node_labels(item.ip),
                }, indent=4)
                return
        raise CommandError("Unkown node name %s" % node)

    @classmethod
    def __get_node_labels(self, node_ip):
        r = requests.get('http://{}:2375/info'.format(node_ip), timeout=5)
        labels = r.json()['Labels']

        if labels is None:
            return []

        return labels

    @classmethod
    @expects_obj
    @arg('nodes', nargs='+', help="the nodes need to add [example: node2:192.168.77.22]")
    @arg('-p', '--playbooks', required=True)
    @arg('-P', '--ssh-port', default=22, help="SSH port of the node to be added")
    @arg('-l', '--labels', nargs='+', default="", help="The labels added to docker daemon in the node. [example: disk=ssd]")
    @arg('-d', '--docker-device', default="", help="The block device use for docker's devicemapper storage."
         "docker will run on loop-lvm if this is not given, which is not proposed")
    def add(self, args):
        """add a new node to lain"""
        try:
            nodes = dict()
            nodes = self.__check_nodes_validation(args.nodes)

            port = args.ssh_port
            for name, ip in nodes:
                check_call(['etcdctl', 'set',
                            '/lain/nodes/new/%s:%s:%s' % (name, ip, port),
                            ip])
                copy_public_key(ip)

            if run_addnode_ansible(args):
                error("run add node ansible failed")
                return

            for name, ip in nodes:
                node_data = json.dumps({'name': name,
                                        'ip': ip,
                                        'ssh_port': port,
                                        'docker_device': args.docker_device})
                check_call(['etcdctl', 'set',
                            '/lain/nodes/nodes/%s:%s:%s' % (name, ip, port),
                            node_data])
        except Exception as e:
            error(str(e))
        finally:
            for name, ip in nodes:
                check_call(
                    ['etcdctl', 'rm', '/lain/nodes/new/%s:%s:%s' % (name, ip, port)])

    @classmethod
    def __check_nodes_validation(self, nodes):
        try:
            nodes = [x.split(':') for x in nodes]

            if len(set(n[0] for n in nodes)) != len(nodes):
                raise AddNodeException("There are duplicate node names")
            if len(set(n[1] for n in nodes)) != len(nodes):
                raise AddNodeException("There are duplicate node IPs")

            if os.getuid() != 0:
                raise AddNodeException(
                    "Need run add-node script with root privilege please.")

            duplicates = self.__check_existing(nodes)
            if duplicates:
                raise AddNodeException(
                    "Some nodes already exist in the cluster: " + ", ".join(duplicates))
        except ValueError:
            raise AddNodeException("the value of param nodes is wrong")
        except IndexError:
            raise AddNodeException(
                "error parse param nodes, needs like 'node2:192.168.77.22'")

        return nodes

    @classmethod
    def __check_existing(self, nodes):
        duplicates = set()

        output = check_output(['etcdctl', 'ls', '/lain/nodes/nodes'])
        for line in output.splitlines():
            key = line.split('/')[-1]
            name, ip, port = key.split(':')
            for node_name, node_ip in nodes:
                if node_name == name:
                    duplicates.add(node_name)
                elif node_ip == ip:
                    duplicates.add(node_ip)

        return duplicates

    @classmethod
    @arg('-p', '--playbooks', required=True)
    @arg('-t', '--target')
    @arg('nodename')
    def remove(self, nodename, target="", playbooks=""):
        """
        remove a node in lain, --target is only useful when swarm manager running on this node.
        """
        node = NodeInfo(nodename)
        target = Node(target) if target != "" else None
        key = "%s:%s:%s" % (node.name, node.ip, node.ssh_port)

        output = check_output(
            ['etcdctl', 'ls', '/lain/nodes/nodes'], stderr=STDOUT)
        if len(output.splitlines()) == 1:
            error("%s is the last node of lain, can not be removed" %
                  output.splitlines()[0].split('/')[-1])
            return

        check_output(['etcdctl', 'set', '/lain/nodes/removing/%s' %
                      key, ""], stderr=STDOUT)

        try:
            assert_etcd_member(node.name)  # check if the node is a etcd member
            info("Remove the lain node %s" % node.name)
            if not yes_or_no("Are you sure?", default='no', color=_yellow):
                raise(RemoveException("Action was canceled"))
            # restart a new swarm manager if a swarm mansger on this node
            drift_swarm_manager(playbooks, node, target)
            remove_node_containers(node.name)
            if run_removenode_ansible(playbooks):
                error("run remove node ansible failed")
                return
            # remove maintain for node
            self.maintain(node.name, True)
            check_call(['etcdctl', 'rm', '/lain/nodes/nodes/%s' %
                        key], stderr=STDOUT)  # remove the node from etcd
        except RemoveException as e:
            error(str(e))
        finally:
            check_output(
                ['etcdctl', 'rm', '/lain/nodes/removing/%s' % key], stderr=STDOUT)
        return

    @classmethod
    @arg('-p', '--playbooks', required=True)
    @arg('nodes', nargs='+')
    def clean(self, nodes, playbooks=""):
        """
        clean node will clean lain node, remove some useless images,
        each container on the node will retain at most 3 latest images on the node.
        """
        for node in nodes:
            node_info = NodeInfo(node)
            key = "%s:%s:%s" % (
                node_info.name, node_info.ip, node_info.ssh_port)
            check_output(['etcdctl', 'set', '/lain/nodes/clean/%s' % key, node_info.ip],
                         stderr=STDOUT)
            run_cleannode_ansible(playbooks)
            check_output(['etcdctl', 'rm', '/lain/nodes/clean/%s' % key])

    @classmethod
    @arg('nodename')
    @arg('-r', '--remove', help="whether removing deployment constraint on the specified node")
    def maintain(self, nodename, remove=False):
        """
        maintain node will disable or enable deployment onto the maintained node.
        """
        node = NodeInfo(nodename)
        base_url = "http://deployd.lain:9003/api/constraints"
        operator = "Remove" if remove else "Add"
        if not remove:
            url = base_url + "?type=node&value=%s" % node.name
            info("PATCH %s" % url)
            resp = requests.patch(url)
        else:
            url = base_url + "?type=node&value=%s" % node.name
            info("DELETE %s" % url)
            resp = requests.delete(url)
        if resp.status_code >= 300:
            error("%s constraint on node %s fail: %s" %
                  (operator, node.name, resp.text))
        else:
            info("%s constraint on node %s success." % (operator, node.name))

    @classmethod
    def health(cls):
        health = NodeHealth()
        health.run()

    @classmethod
    @arg('nodes', nargs='+')
    @arg('-c', '--change-type', choices=['add', 'delete'], required=True)
    @arg('-l', '--labels', nargs='+', type=str, required=True,
         help='the labels to add, for example: k1=v1 k2=v2')
    @arg('-p', '--playbooks', required=True)
    def change_labels(self, nodes, change_type="", labels=[], playbooks=""):
        """
        change labels of nodes, add/delete operations are supported
        """
        normlized_labels = {}
        for x in labels:
            ys = x.split('=')
            if len(ys) != 2 or ys[0].strip() == '' or ys[1].strip() == '':
                error('{} is not $k=$v format'.format(x))
                sys.exit(1)

            key, value = ys[0].strip(), ys[1].strip()
            if key in normlized_labels:
                error('duplicate key {}'.format(key))
                sys.exit(1)

            normlized_labels[key] = value
        diff_labels = ['{}={}'.format(k, v)
                       for k, v in normlized_labels.items()]

        try:
            for node in nodes:
                node_info = NodeInfo(node)
                key = "{}:{}:{}".format(node_info.name, node_info.ip,
                                        node_info.ssh_port)
                check_output(['etcdctl', 'set',
                              '/lain/nodes/changing_labels/{}'.format(key),
                              node_info.ip], stderr=STDOUT)
            run_change_labels_ansible(playbooks, change_type, diff_labels)
        except Exception as e:
            error("Exception: {}.".format(e))
            sys.exit(1)
        finally:
            for node in nodes:
                node_info = NodeInfo(node)
                key = "{}:{}:{}".format(node_info.name, node_info.ip,
                                        node_info.ssh_port)
                check_output(['etcdctl', 'rm',
                              '/lain/nodes/changing_labels/{}'.format(key)],
                             stderr=STDOUT)


def run_addnode_ansible(args):
    envs = {
        'target': 'new_nodes',
        'allow_restart_docker': 'yes',
        'adding_node_mode': 'yes'  # this ensures the removal of existing key.json
    }
    if args.docker_device:
        envs['docker_device'] = args.docker_device
    if args.labels:
        envs['node_labels'] = args.labels
    return run_ansible_cmd(args.playbooks, envs, file_name='site.yaml')


def run_change_labels_ansible(playbooks_path, change_type, diff_labels):
    envs = {
        'target': 'changing_labels_nodes',
        'role': 'node-change-labels',
        'change_type': change_type,
        'diff_labels': diff_labels
    }
    return run_ansible_cmd(playbooks_path, envs)


def run_cleannode_ansible(playbooks_path):
    envs = {
        'target': 'clean_nodes',
        'role': 'node-clean'
    }
    return run_ansible_cmd(playbooks_path, envs)


def run_removenode_ansible(playbooks_path):
    envs = {
        'target': 'removing_nodes',
        'role': 'remove-node',
    }
    return run_ansible_cmd(playbooks_path, envs)


def drift_swarm_manager(playbooks_path, rm_node, target):
    is_swarm_manager, key = False, "%s:%s:%s" % (
        rm_node.name, rm_node.ip, rm_node.ssh_port)
    output = check_output(['etcdctl', 'ls', '/lain/nodes/swarm-managers'])
    for line in output.splitlines():
        if line.split('/')[-1] == key:
            is_swarm_manager = True
            break
    if not is_swarm_manager:
        return

    if not target:
        raise(RemoveException("%s is a swarm manager node,"
                              "target required to drift the swarm manager,"
                              "run `remove-node clear -t[--target] ...`" % rm_node.name))

    check_call(['etcdctl', 'rm', '/lain/nodes/swarm-managers/%s' % key])
    check_call(['etcdctl', 'set', '/lain/nodes/swarm-managers/%s:%s' %
                (target.name, target.ssh_port), ""])

    envs = dict()
    envs['target'] = 'nodes'
    envs['role'] = 'swarm'
    info('The removed node is a swarm manager, now start a swarm manager on another node.')
    run_ansible_cmd(playbooks_path, envs)


def remove_node_containers(nodename):
    url = "http://deployd.lain:9003/api/nodes?node=%s" % nodename

    # Call deployd api
    info("DELETE %s" % url)
    resp = requests.delete(url)
    if resp.status_code >= 300:
        error("Deployd remove node api response a error, %s." % resp.text)

    # waiting for deployd complete
    print(">>>(need some minutes)Waiting for deployd drift %s's containers" % nodename)
    while True:
        try:
            output = Popen(['docker', '-H', 'swarm.lain:2376', 'ps',
                            '-a', '-f', 'node=%s' % nodename, '-f',
                            'label=com.docker.swarm.id'], stdout=PIPE)
            exclude_portals = check_output(
                ['grep', '-v', '.portal.portal'], stdin=output.stdout)
            containers = len(exclude_portals.splitlines()) - 1
            if containers > 0:
                warn("%d containers in node %s need to drift" %
                     (containers, nodename))
                time.sleep(3)
            else:
                info("all containers in node %s drifted successed" % nodename)
                break
        except Exception as e:
            info('check containers info with err:%s' % e)
            time.sleep(3)


def assert_etcd_member(rm_node):
    output = check_output(['etcdctl', 'member', 'list'])
    node_name = rm_node.split(':')[0]
    for line in output.splitlines():
        if node_name == line.split()[1].split('=')[1]:
            raise RemoveException("%s is a etcd member, you should remove it from "
                                  "etcd cluster before remove it from lain" % rm_node)


def copy_public_key(ip):
    cmd = ['sudo', 'ssh-copy-id', '-i', '/root/.ssh/lain.pub']
    cmd += ['root@%s' % ip]
    info('run cmd: %s', ' '.join(cmd))
    check_output(cmd)
