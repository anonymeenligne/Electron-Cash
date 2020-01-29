#!/usr/bin/env python3
#
# Electron Cash - a lightweight Bitcoin Cash client
# CashFusion - an advanced coin anonymizer
#
# Copyright (C) 2020 Mark B. Lundeberg
# Copyright (C) 2020 Calin A. Culianu
#
# Permission is hereby granted, free of charge, to any person
# obtaining a copy of this software and associated documentation files
# (the "Software"), to deal in the Software without restriction,
# including without limitation the rights to use, copy, modify, merge,
# publish, distribute, sublicense, and/or sell copies of the Software,
# and to permit persons to whom the Software is furnished to do so,
# subject to the following conditions:
#
# The above copyright notice and this permission notice shall be
# included in all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND,
# EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF
# MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND
# NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS
# BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN
# ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN
# CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.
"""
CashFusion - conf.py - configuration & settings management
"""
from collections import namedtuple
from typing import List, Optional, Tuple, Union

class Conf:
    ''' A class that's a simple wrappr around CashFusion per-wallet settings
    stored in wallet.storage.  The intended usecase is for outside code
    to construct these object as needed to read a key, e.g.:
            b = Conf(wallet).autofuse     # getter
            Conf(wallet).autofuse = True  # setter
    '''

    class Defaults:
        Autofuse = False
        AutofuseConfirmedOnly = False
        QueudAutofuse = 4
        Selector = ('fraction', 0.1)  # coin selector options
        SelfFusePlayers = 1 # self-fusing control (1 = just self, more than 1 = self fuse up to N times)


    def __init__(self, wallet):
        ''' The wallet arg is required.  The config arg is optional but if
        not specified, global configuration options are not available. '''
        assert wallet
        self.wallet = wallet

    @property
    def autofuse(self) -> bool:
        return bool(self.wallet.storage.get('cashfusion_autofuse', self.Defaults.Autofuse))
    @autofuse.setter
    def autofuse(self, b : Optional[bool]):
        if b is not None: b = bool(b)
        self.wallet.storage.put('cashfusion_autofuse', b)

    @property
    def autofuse_confirmed_only(self) -> bool:
        return bool(self.wallet.storage.get('cashfusion_autofuse_only_when_all_confirmed', self.Defaults.AutofuseConfirmedOnly))
    @autofuse_confirmed_only.setter
    def autofuse_confirmed_only(self, b : Optional[bool]):
        if b is not None: b = bool(b)
        self.wallet.storage.put('cashfusion_autofuse_only_when_all_confirmed', b)

    @property
    def queued_autofuse(self) -> int:
        return int(self.wallet.storage.get('cashfusion_queued_autofuse', self.Defaults.QueudAutofuse))
    @queued_autofuse.setter
    def queued_autofuse(self, i : Optional[int]):
        if i is not None:
            assert i >= 1
            i = int(i)
        self.wallet.storage.put('cashfusion_queued_autofuse', i)

    @property
    def selector(self) -> Tuple[str, Union[int,float]]:
        return tuple(self.wallet.storage.get('cashfusion_selector', self.Defaults.Selector))
    @selector.setter
    def selector(self, t : Optional[ Tuple[str, Union[int,float]] ]):
        ''' Optional: Pass None to clear the key '''
        assert t is None or (isinstance(t, (tuple, list)) and len(t) == 2)
        self.wallet.storage.put('cashfusion_selector', t)

    @property
    def self_fuse_players(self) -> int:
        return int(self.wallet.storage.get('cashfusion_self_fuse_players', self.Defaults.SelfFusePlayers))
    @self_fuse_players.setter
    def self_fuse_players(self, i : Optional[int]):
        if i is not None:
            assert i >= 1
            i = int(i)
        return self.wallet.storage.put('cashfusion_self_fuse_players', i)

CashFusionServer = namedtuple("CashFusionServer", ('hostname', 'port', 'ssl'))

def _get_default_server_list() -> List[Tuple[str, int, bool]]:
    ''' Maybe someday this can come from a file or something.  But can also
    always be hard-coded.

        Tuple fields: (hostname: str, port: int, ssl: bool)
    '''
    return [
        # first one is the default
        CashFusionServer('89.40.7.97', 8787, False),
        CashFusionServer('server2.example.com', 3436, True),
    ]


class Global:
    ''' A class that's a simple wrappr around CashFusion global settings
    stored in the app-wide config object.  The intended usecase is for outside
    code to construct these object as needed to read a key, e.g.:
            h = Global(config).tor_host            # getter
            Global(config).tor_host = 'localhost'  # setter
    '''
    class Defaults:
        ServerList : List[Tuple[str, int, bool]] = _get_default_server_list()
        TorHost = 'localhost'
        TorPortAuto = True
        TorPortManual = 9050


    def __init__(self, config):
        assert config
        self.config = config

    @property
    def server(self) -> Tuple[str, int, bool]:
        return tuple(self.config.get('cashfusion_server', self.Defaults.ServerList[0]))
    @server.setter
    def server(self, t : Optional[Tuple[str, int, bool]]):
        if t is not None:
            assert isinstance(t, (list, tuple)) and len(t) == 3
            t = CashFusionServer(*t)
            assert isinstance(t.hostname, str)
            assert isinstance(t.port, int)
            assert isinstance(t.ssl, bool)
        self.config.set_key('cashfusion_server', t)

    @property
    def tor_host(self) -> str:
        return str(self.config.get('cashfusion_tor_host', self.Defaults.TorHost))
    @tor_host.setter
    def tor_host(self, h : Optional[str]):
        if h is not None:
            h = str(h)
            assert h
        self.config.set_key('cashfusion_tor_host', h)

    @property
    def tor_port_auto(self) -> bool:
        return bool(self.config.get('cashfusion_tor_port_auto', self.Defaults.TorPortAuto))
    @tor_port_auto.setter
    def tor_port_auto(self, b : Optional[bool]):
        if b is not None:
            b = bool(b)
        self.config.set_key('cashfusion_tor_port_auto', b)

    @property
    def tor_port_manual(self) -> int:
        return int(self.config.get('cashfusion_tor_port_manual', self.Defaults.TorPortManual))
    @tor_port_manual.setter
    def tor_port_manual(self, i : Optional[int]):
        if i is not None:
            i = int(i)
            assert 0 <= i <= 65535
        self.config.set_key('cashfusion_tor_port_manual', i)
