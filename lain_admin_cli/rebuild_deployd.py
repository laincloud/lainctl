# -*- coding: utf-8 -*-

from argh.decorators import arg
from subprocess import check_call
from lain_admin_cli.helpers import run_ansible_cmd
from lain_admin_cli.helpers import yes_or_no, _yellow
import os

@arg('-p', '--playbooks', required=True)
def rebuild_deployd(playbooks="", sso_url="https://sso.yxapp.in"):
    """
    recreate the layer1-deployd-app, by using a temporary layer0-deployd
    """
    if not yes_or_no(prompt="Remove the deployd-app and create a new one. Are you sure?", color=_yellow):
        return
    envs = {
        'role': 'deploy-rebuild',
    }
    run_ansible_cmd(playbooks, envs)
