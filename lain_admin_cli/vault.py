# -*- coding: utf-8 -*-

import requests
import json
import sys
from argh.decorators import arg, expects_obj

from lain_admin_cli.helpers import (
    TwoLevelCommandBase, warn, info, error
)

KEY_FILE_PATH = '/root/.lvault_key'


class Vault(TwoLevelCommandBase):

    @classmethod
    def subcommands(self):
        return [self.init, self.unseal, self.status]

    @classmethod
    def namespace(self):
        return "vault"

    @classmethod
    def help_message(self):
        return "lain vault operations"

    @classmethod
    @expects_obj
    @arg('-s', '--secret_shares', default=1, help='The number of shares to split the master key into')
    @arg('-t', '--secret_threshold', default=1, help='The number of shares required to reconstruct the master key. This must be less than or equal to secret_shares')
    @arg('-S', '--save', default=False, help='whether to save the master key and root token as a local file which path is "output_file" or not')
    @arg('-o', '--output_file', default=KEY_FILE_PATH, help='the output file path, valid when save is true')
    def init(self, args):
        """
        vault init will initialize the vault cluster on lain; and the admin should do it once unless the data in the store backend (such as etcd) has been destroyed. 
        """
        if args.save:
            warn(
                'you should keep the vault key only in the memory or somewhere much more private')

        if args.secret_shares < 1 or args.secret_threshold < 1 or args.secret_shares < args.secret_threshold:
            error('invalid parameter')
            sys.exit(1)

        init_vault(args)

    @classmethod
    @expects_obj
    @arg('-c', '--content', help='the keys and root token which returned as a result of "lain vault init"; if you specify the content, the "key_file" parameter is ignored; content type: json')
    @arg('-f', '--key_file', default=KEY_FILE_PATH, help='the file which give the keys and root token')
    def unseal(self, args):
        """
        vault unseal will unseal all the sealed vault instances using the keys and update the root token which is stored in the lvault. 
        """
        if args.content and args.key_file != KEY_FILE_PATH:
            error('can not use both content and key_file')
            sys.exit(1)
        else:
            unseal_all_lvault(args)

    @classmethod
    def status(self):
        """
        vault status will give the status of vault cluster and the lvault cluster;
        """
        vault_status = requests.get("http://lvault.lain.local/v2/vaultstatus")
        info(vault_status.text)
        lvault_status = requests.get("http://lvault.lain.local/v2/status")
        info(lvault_status.text)


def init_vault(args):
    lvault_url = "http://lvault.lain.local/v2/init"
    payload = {"secret_threshold": args.secret_threshold,
               "secret_shares": args.secret_shares}
    init_response = requests.put(lvault_url, json=payload)
    # info("%s",init_response.status_code)
    if init_response.status_code == 200:
        if args.save:
            info(
                "the unseal keys and root token have been saved in the file %s", args.output_file)
            with open(args.output_file, "w") as f:
                f.write(init_response.text)
        else:
            info(
                "The following keys and token will be returned only once. Please save them somewhere.")
            info(init_response.text)
    elif init_response.status_code == 400:
        error("%s", init_response.text)
    else:
        error("strange response from server: %s, %s",
              init_response.status_code, init_response.text)


def unseal_all_lvault(args):
    roottoken_keys = get_roottoken_keys(args)
    if roottoken_keys is None:
        error("can not get root_token and keys")
        sys.exit(1)
    reset_lvault(roottoken_keys)

    lvault_url = "http://lvault.lain.local/v2/unsealall"
    payload = {}
    unseal_response = requests.put(lvault_url, json=payload)
    if unseal_response.status_code != 200:
        error("%s", unseal_response.text)
        sys.exit(1)

    reset_lvault(roottoken_keys)


def get_roottoken_keys(args):
    if args.content:
        return json.loads(args.content)
    else:
        with open(args.key_file, 'r') as f:
            return json.load(f)
    return None


def reset_lvault(roottoken_keys):
    lvault_url = "http://lvault.lain.local/v2/reset"
    reset_response = requests.put(lvault_url, json=roottoken_keys)
    if reset_response.status_code != 200:
        error("%s", reset_response.text)
        sys.exit(1)
    # info("%s",reset_response.status_code)
