# -*- coding: utf-8 -*-
import logging
import argh
import os
from lain_admin_cli.version import version
from lain_admin_cli.node import Node
from lain_admin_cli.config import Config
from lain_admin_cli.cluster import Cluster
from lain_admin_cli.auth import Auth
from lain_admin_cli.drift import drift
from lain_admin_cli.bootstrap import bootstrap

logging.getLogger("requests").setLevel(logging.WARNING)
logging.getLogger("docker").setLevel(logging.WARNING)

one_level_commands = [
    version, drift
]

two_level_commands = [
        Node, Cluster, Auth
]

def main():
    parser = argh.ArghParser()
    parser.add_commands(one_level_commands)
    for command in two_level_commands:
        argh.add_commands(parser, command.subcommands(), namespace=command.namespace(), help=command.help_message())
    parser.dispatch()


if __name__ == "__main__":
    main()
