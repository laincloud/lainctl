# -*- coding: utf-8 -*-

from argh.decorators import arg
from lain_admin_cli.helpers import Node, Container, is_backupd_enabled
from lain_admin_cli.helpers import yes_or_no, info, error, warn, _yellow, volume_dir
from subprocess import check_output, check_call, CalledProcessError
import requests, os, json, time


@arg('-p', '--playbooks', required=True)
@arg('--with-volume')
@arg('--ignore-volume')
@arg('-t', '--target')
@arg('containers', nargs='+')
def drift(containers, with_volume=False, ignore_volume=False, playbooks="", target=""):
    if with_volume and ignore_volume:
        error("--with-volume and --ignore-volume is mutual option")
        return
    target = Node(target) if target != "" else None
    try:
        containers = [Container(c) for c in containers]
        nodes = [Node(c.host) for c in containers]
    except Exception as e:
            error("Fail to get container or node info, %s" % (str(e)))
            return

    info("Drifting %s to %s", ["%s/%s" % (c.host, c.name) for c in containers],
         target.name if target else "a random node")

    if not yes_or_no("Are you sure?", default='no', color=_yellow):
        return

    for container in containers:
        if len(container.volumes) > 0:
            if not (with_volume or ignore_volume):
                warn("container %s having lain volumes,"
                     "you may need run `drift --ignore-volume[--with-volume] ...` to drift it,"
                     "ignore this container." % container.name)
                continue
            if not target and with_volume:
                warn("container %s having lain volumes, target node required to drift with volume." % container.name)
                warn("run `drift --with-volume -t[--target] somenode ...`")
                continue

        node = Node(container.host)
        drift_container(node, container, target, playbooks, with_volume, ignore_volume)
        if len(container.volumes) > 0 and is_backupd_enabled():
            fix_backupd(container, node, target)


def fix_backupd(container, source, target):
    try:
        tf = open("/mfs/lain/backup/%s/.meta" % target.ip, 'rb')
    except IOError as e:
        target_meta = {}
    else:
        target_meta = json.loads(tf.read())
        tf.close()

    try:
        sf = open("/mfs/lain/backup/%s/.meta" % source.ip, 'rb')
    except IOError as e:
        return # backup file do not exist
    else:
        source_meta = json.loads(sf.read())
        sf.close()

    changed = False
    for volume in container.volumes:
        data = source_meta.get(volume, None)
        if not data:
            continue
        target_meta[volume] = []
        for item in data:
            info("Fix backup for %s" % item['name'])
            source_file = "/mfs/lain/backup/%s/%s" % (source.ip, item['name'])
            target_file = "/mfs/lain/backup/%s/%s" % (target.ip, item['name'])
            try:
                cmd = 'mkdir -p /mfs/lain/backup/%s && cp -r %s %s' % (target.ip, source_file, target_file)
                check_call(['/bin/bash', '-c', cmd])
            except CalledProcessError as e:
                error(str(e))
                warn("You may need to move %s to %s by hands" % (source_file, target_file))
                continue
            else:
                target_meta[volume].append(item)
                changed = True
    if changed:
        try:
            tf = open("/mfs/lain/backup/%s/.meta" % target.ip, 'w+')
            tf.write(json.dumps(target_meta))
            tf.close()
        except IOError as e:
            warn(str(e))
            warn("Fail to create meta on target node, check this by hand" % target.ip)


def drift_volumes(playbooks_path, containers, source, target):
    volumes = reduce(lambda x, y: x + y.volumes, containers, [])
    ids = reduce(lambda x, y: x + [y.info['Id']], containers, [])
    var_file = "/tmp/ansible-variables"

    with open(var_file, 'wb') as f:
        f.write('{"volumes":%s,"ids":"%s"}'%(json.dumps(volumes), ' '.join(ids)))

    cmd = ['ansible-playbook', '-i', os.path.join(playbooks_path, 'cluster')]
    cmd += ['-e', 'target=nodes']
    cmd += ['-e', 'target_node=%s'%target.name]
    cmd += ['-e', 'from_node=%s'%source.name]
    cmd += ['-e', 'from_ip=%s'%source.ip]
    cmd += ['-e', 'role=drift']
    cmd += ['-e', 'var_file=%s'%var_file]
    cmd += [os.path.join(playbooks_path, 'role.yaml')]
    info('cmd is: %s', ' '.join(cmd))
    check_call(cmd)
    os.remove(var_file)


def warm_up_on_target(playbooks_path, containers, target):
    to_drift_images = reduce(lambda x, y: x + [y.info['Config']['Image']],
                             containers, [])

    cmd = ['ansible-playbook', '-i', os.path.join(playbooks_path, 'cluster')]
    cmd += ['-e', 'target=nodes']
    cmd += ['-e', 'target_node=%s' % target.name]
    cmd += ['-e', 'role=drift-warm-up']
    cmd += ['-e', 'to_drift_images=%s' % to_drift_images]
    cmd += [os.path.join(playbooks_path, 'role.yaml')]
    info('cmd is: %s', ' '.join(cmd))
    check_call(cmd)


def drift_container(from_node, container, to_node, playbooks_path, with_volume, ignore_volume):
    if container.appname == 'deploy':
        key = '/lain/deployd/pod_groups/deploy/deploy.web.web'
        data = json.loads(check_output(['etcdctl', 'get', key]))
        if len(data['Pods']) <= 1:
            warn("Deployd is not HA now, can not drift it."
                 "you should scale it to 2+ instance first."
                 "ignore container %s" % container.name)
            return
    elif container.appname == 'webrouter':
        if not yes_or_no("Make sure %s exist on %s" % (container.info['Config']['Image'], to_node.name),
                         default='no', color=_yellow):
            return

    url = "http://deployd.lain:9003/api/nodes?cmd=drift&from=%s&pg=%s&pg_instance=%s" % (
        from_node.name, container.podname, container.instance
    )
    url += "&force=true" if with_volume or ignore_volume else ""
    url += "&to=%s" % to_node.name if to_node else ""

    if to_node:
        ## Warm-up on target node
        info("Warm-up on target node...")
        warm_up_on_target(playbooks_path, [container], to_node)
    else:
        info("No specified target node, skip warm-up...")

    ## Drift volumes
    if with_volume and len(container.volumes) > 0:
        info("Drift the volume...")
        drift_volumes(playbooks_path, [container], from_node, to_node)

        info("Stop the container %s" % container.name)
        try:
            check_output(['docker', '-H', 'swarm.lain:2376', 'stop', container.info['Id']])
        except CalledProcessError:
            # container may not existed now, removed by deployd, ignore errors
            error("Fail to stop the container %s" % container.name)
            return

        info("Drift the volume again...")
        drift_volumes(playbooks_path, [container], from_node, to_node)

    ## Call deployd api
    info("PATCH %s" % url)
    resp = requests.patch(url)
    if resp.status_code >= 300:
        error("Deployd drift api response a error, %s." % resp.text)

    ## waiting for deployd complete
    drifted_container_name = "%s.%s.%s.v%s-i%s-d%s" % (
        container.appname, container.proctype, container.procname,
        container.version, container.instance, container.drift+1
    )
    print(">>>(need some minutes)Waiting for deployd drift %s to %s..." % (container.name, drifted_container_name))
    while True:
        try:
            output = check_output(['docker', '-H', 'swarm.lain:2376', 'inspect', drifted_container_name])
        except CalledProcessError:
            time.sleep(3)
        else:
            new_container = json.loads(output)[0]
            info("%s/%s => %s%s drifted success" % (container.host, container.name,
                                                    new_container['Node']['Name'],
                                                    new_container['Name']))
            break
