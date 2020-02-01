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
Base plugin (non-GUI)
"""
import math
import threading
import time
import weakref

from electroncash.bitcoin import COINBASE_MATURITY
from electroncash.plugins import BasePlugin, hook, daemon_command
from electroncash.i18n import _, ngettext, pgettext
from electroncash.util import profiler, PrintError, InvalidPassword
from electroncash import Network

from .conf import Conf, Global
from .fusion import Fusion, can_fuse_from, can_fuse_to, is_tor_port
from .server import FusionServer
from .covert import limiter

import random  # only used to select random coins

TOR_PORTS = [9050, 9150]
# if more than <N> tor connections have been made recently (see covert.py) then don't start auto-fuses.
AUTOFUSE_RECENT_TOR_LIMIT_LOWER = 60
# if more than <N> tor connections have been made recently (see covert.py) then shut down auto-fuses that aren't yet started
AUTOFUSE_RECENT_TOR_LIMIT_UPPER = 120

# heuristic factor: guess that expected number of coins in wallet in equilibrium is = (this number) / fraction
COIN_FRACTION_FUDGE_FACTOR = 10
# for semi-linked addresses (that share txids in their history), allow linking them with this probability:
KEEP_LINKED_PROBABILITY = 0.1

# how long an auto-fusion may stay in 'waiting' state (without starting-soon) before it cancels itself
AUTOFUSE_INACTIVE_TIMEOUT = 600

# how many random coins to select max in 1 batch -- used by select_random_coins
DEFAULT_MAX_COINS = 20
assert DEFAULT_MAX_COINS > 5

pnp = None
def get_upnp():
    """ return an initialized UPnP singleton """
    global pnp
    if pnp is not None:
        return pnp
    try:
        import miniupnpc
    except ImportError:
        raise RuntimeError("python miniupnpc module not installed")
    u = miniupnpc.UPnP()
    if u.discover() < 1:
        raise RuntimeError("can't find UPnP server")
    try:
        u.selectigd()
    except Exception as e:
        raise RuntimeError("failed to connect to UPnP IGD")
    pnp = u
    return u

def select_coins(wallet):
    """ Sort the wallet's coins into address buckets, returning two lists:
    - Eligible addresses and their coins.
    - Ineligible addresses and their coins.

    An address is eligible if it satisfies all conditions:
    - the address is unfrozen
    - has 1, 2, or 3 utxo
    - all utxo are confirmed (or matured in case of coinbases)
    - has no SLP utxo or frozen utxo
    """
    # First, select all the coins
    eligible = []
    ineligible = []
    has_unconfirmed = False
    has_coinbase = False
    sum_value = 0
    mincbheight = (wallet.get_local_height() + 1 - COINBASE_MATURITY if Conf(wallet).autofuse_coinbase
                   else -1)  # -1 here causes coinbase coins to always be rejected
    for addr in wallet.get_addresses():
        acoins = list(wallet.get_addr_utxo(addr).values())
        if not acoins:
            continue  # prevent inserting empty lists into eligible/ineligible
        good = True
        if addr in wallet.frozen_addresses:
            good = False
        for i,c in enumerate(acoins):
            sum_value += c['value']  # tally up values regardless of eligibility
            # If too many coins, any SLP tokens, any frozen coins, or any
            # immature coinbase on the address -> flag all address coins as
            # ineligible if not already flagged as such.
            good = good and (
                i <= 3  # must not have too many coins on the same address*
                and not c['slp_token']  # must not be SLP
                and not c['is_frozen_coin']  # must not be frozen
                and (not c['coinbase'] or c['height'] <= mincbheight)  # if coinbase -> must be mature coinbase
            )
            # * = We skip addresses with too many coins, since they take up lots
            #     of 'space' for consolidation. TODO: there is possibility of
            #     disruption here, if we get dust spammed. Need to deal with
            #     'dusty' addresses by ignoring / consolidating dusty coins.

            # Next, detect has_unconfirmed & has_coinbase:
            if c['height'] <= 0:
                # Unconfirmed -> Flag as not eligible and set the has_unconfirmed flag.
                good = False
                has_unconfirmed = True
            # Update has_coinbase flag if not already set
            has_coinbase = has_coinbase or c['coinbase']
        if good:
            eligible.append((addr,acoins))
        else:
            ineligible.append((addr,acoins))

    return eligible, ineligible, int(sum_value), bool(has_unconfirmed), bool(has_coinbase)

def select_random_coins(wallet, fraction, eligible):
    """
    Grab wallet coins with a certain probability, while also paying attention
    to obvious linkages and possible linkages.
    Returns list of list of coins (bucketed by obvious linkage).
    """
    # First, we want to bucket coins together when they have obvious linkage.
    # Coins that are linked together should be spent together.
    # Currently, just look at address.
    addr_coins = eligible
    random.shuffle(addr_coins)

    # While fusing we want to pay attention to semi-correlations among coins.
    # When we fuse semi-linked coins, it increases the linkage. So we try to
    # avoid doing that (but rarely, we just do it anyway :D).
    # Currently, we just look at all txids touched by the address.
    # (TODO this is a disruption vector: someone can spam multiple fusions'
    #  output addrs with massive dust transactions (2900 outputs in 100 kB)
    #  that make the plugin think that all those addresses are linked.)
    result_txids = set()

    result = []
    num_coins = 0
    for addr, acoins in addr_coins:
        if num_coins >= DEFAULT_MAX_COINS:
            break
        elif num_coins + len(acoins) > DEFAULT_MAX_COINS:
            continue

        # For each bucket, we give a separate chance of joining.
        if random.random() > fraction:
            continue

        # Semi-linkage check:
        # We consider all txids involving the address, historical and current.
        ctxids = {txid for txid, height in wallet.get_address_history(addr)}
        collisions = ctxids.intersection(result_txids)
        # Note each collision gives a separate chance of discarding this bucket.
        if random.random() > KEEP_LINKED_PROBABILITY**len(collisions):
            continue
        # OK, no problems: let's include this bucket.
        num_coins += len(acoins)
        result.append(acoins)
        result_txids.update(ctxids)

    if not result:
        # nothing was selected, just try grabbing first nonempty bucket
        try:
            res = next(coins for addr,coins in addr_coins if coins)
            result = [res]
        except StopIteration:
            # all eligible buckets were cleared.
            pass

    return result

def get_target_params_1(wallet, eligible):
    """ WIP -- TODO: Rename this function. """
    wallet_conf = Conf(wallet)
    mode = wallet_conf.fusion_mode

    get_n_coins = lambda: sum(len(acoins) for addr,acoins in eligible)
    if mode == 'normal':
        n_coins = get_n_coins()
        return max(2, round(n_coins / DEFAULT_MAX_COINS)), False
    elif mode == 'fan-out':
        n_coins = get_n_coins()
        return max(4, math.ceil(n_coins / (COIN_FRACTION_FUDGE_FACTOR*0.65))), False
    elif mode == 'consolidate':
        n_coins = get_n_coins()
        num_threads = math.trunc(n_coins / (COIN_FRACTION_FUDGE_FACTOR*1.5))
        return num_threads, num_threads <= 1
    else:  # 'custom'
        target_num_auto = wallet_conf.queued_autofuse
        confirmed_only = wallet_conf.autofuse_confirmed_only
        return target_num_auto, confirmed_only

def get_target_params_2(wallet, eligible, sum_value):
    """ WIP -- TODO: Rename this function. """
    wallet_conf = Conf(wallet)
    mode = wallet_conf.fusion_mode

    fraction = 0.1

    if mode == 'custom':
        # Determine the fraction that should be used
        select_type, select_amount = wallet_conf.selector

        if select_type == 'size' and int(sum_value) != 0:
            # user wants to get a typical output of this size (in sats)
            fraction = COIN_FRACTION_FUDGE_FACTOR * select_amount / sum_value
        elif select_type == 'count' and int(select_amount) != 0:
            # user wants this number of coins
            fraction = COIN_FRACTION_FUDGE_FACTOR / select_amount
        elif select_type == 'fraction':
            # user wants this fraction
            fraction = select_amount
        # note: fraction at this point could be <0 or >1 but doesn't matter.
    elif mode == 'consolidate':
        fraction = 1.0
    elif mode == 'normal':
        fraction = 0.5
    elif mode == 'fan-out':
        fraction = 0.1

    return fraction


class FusionPlugin(BasePlugin):
    fusion_server = None
    active = True
    _run_iter = 0

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs) # gives us self.config
        self.fusions = weakref.WeakKeyDictionary()
        # Do an initial check on the tor port
        t = threading.Thread(name = 'Fusion-scan_torport_initial', target = self.scan_torport)
        t.start()
        self.scan_torport_thread = weakref.ref(t)
        self.autofusing_wallets = weakref.WeakKeyDictionary()  # wallet -> password
        self.lock = threading.RLock() # always order: plugin.lock -> wallet.lock -> fusion.lock

    def on_close(self,):
        super().on_close()
        self.stop_fusion_server()
        self.active = False

    def fullname(self):
        return 'CashFusion'

    def description(self):
        return _("CashFusion Protocol")

    def get_server(self, ):
        return Global(self.config).server

    def set_server(self, host, port, ssl):
        Global(self.config).server = (host, port, ssl)  # type/sanity checking done in setter

    def get_torhost(self):
        if self.has_auto_torport():
            return Global.Defaults.TorHost
        else:
            return Global(self.config).tor_host

    def set_torhost(self, host):
        ''' host should be a valid hostname '''
        if not host: return
        Global(self.config).tor_host = host

    def has_auto_torport(self, ):
        return Global(self.config).tor_port_auto

    def get_torport(self, ):
        ''' Retreive either manual port or autodetected port; may return None
        if 'auto' mode and no Tor port has been autodetected. (this is non-blocking) '''
        if self.has_auto_torport():
            return self.tor_port_good
        else:
            return Global(self.config).tor_port_manual

    def set_torport(self, port):
        # port may be 'auto' or 'manual' or an int
        gconf = Global(self.config)
        if port == 'auto':
            gconf.tor_port_auto = True
            return
        else:
            gconf.tor_port_auto = False
        if port == 'manual':
            return # we're simply going to use whatever manual port was already set
        assert isinstance(port, int)
        gconf.tor_port_manual = port

    def scan_torport(self, ):
        ''' Scan for Tor proxy on either the manual port or on a series of
        automatic ports. This is blocking. Returns port if it's up, or None if
        down / can't find. '''
        host = self.get_torhost()

        if self.has_auto_torport():
            portlist = []

            network = Network.get_instance()
            if network:
                tc = network.tor_controller
                if tc and tc.is_enabled() and tc.active_socks_port:
                    portlist.append(tc.active_socks_port)

            portlist.extend(TOR_PORTS)
        else:
            portlist = [ Global(self.config).tor_port_manual ]

        for port in portlist:
            if is_tor_port(host, port):
                self.tor_port_good = port
                break
        else:
            self.tor_port_good = None
        return self.tor_port_good

    def disable_autofusing(self, wallet):
        self.autofusing_wallets.pop(wallet, None)
        Conf(wallet).autofuse = False
        running = []
        for f in list(wallet._fusions_auto):
            f.stop('Autofusing disabled', not_if_running = True)
            if f.status[0] == 'running':
                running.append(f)
        return running

    def enable_autofusing(self, wallet, password):
        if password is None and wallet.has_password():
            raise InvalidPassword()
        else:
            wallet.check_password(password)
        self.autofusing_wallets[wallet] = password
        Conf(wallet).autofuse = True

    def is_autofusing(self, wallet):
        return (wallet in self.autofusing_wallets)

    def add_wallet(self, wallet, password=None):
        ''' Attach the given wallet to fusion plugin, allowing it to be used in
        fusions with clean shutdown. Also start auto-fusions for wallets that want
        it (if no password).
        '''
        # all fusions relating to this wallet, in particular the fuse-from type (which have frozen coins!)
        wallet._fusions = weakref.WeakSet()
        # fusions that were auto-started.
        wallet._fusions_auto = weakref.WeakSet()

        if Conf(wallet).autofuse:
            try:
                self.enable_autofusing(wallet, password)
            except InvalidPassword:
                self.disable_autofusing(wallet)

    def remove_wallet(self, wallet):
        ''' Detach the provided wallet; returns list of active fusions. '''
        with self.lock:
            self.autofusing_wallets.pop(wallet, None)
        with wallet.lock:
            fusions = tuple(getattr(wallet, '_fusions', ()))
            try: del wallet._fusions
            except AttributeError: pass
            try: del wallet._fusions_auto
            except AttributeError: pass
        return [f for f in fusions if f.status[0] not in ('complete', 'failed')]


    def start_fusion(self, source_wallet, password, coins, target_wallet = None):
        # Should be called with plugin.lock and wallet.lock
        if target_wallet is None:
            target_wallet = source_wallet # self-fuse
        assert can_fuse_from(source_wallet)
        assert can_fuse_to(target_wallet)
        host, port, ssl = self.get_server()
        if host == 'localhost':
            # as a special exemption for the local fusion server, we don't use Tor.
            torhost = None
            torport = None
        else:
            torhost = self.get_torhost()
            torport = self.get_torport()
            if torport is None:
                torport = self.scan_torport() # may block for a very short time ...
            if torport is None:
                self.notify_server_status(False, ("failed", _("Invalid Tor proxy or no Tor proxy found")))
                raise RuntimeError("can't find tor port")
        fusion = Fusion(self, target_wallet, host, port, ssl, torhost, torport)
        target_wallet._fusions.add(fusion)
        source_wallet._fusions.add(fusion)
        fusion.add_coins_from_wallet(source_wallet, password, coins)
        fusion.start(inactive_timeout = AUTOFUSE_INACTIVE_TIMEOUT)
        self.fusions[fusion] = time.time()
        return fusion


    def thread_jobs(self, ):
        return [self]
    def run(self, ):
        # this gets called roughly every 0.1 s in the Plugins thread; downclock it to 5 s.
        run_iter = self._run_iter + 1
        if run_iter < 50:
            self._run_iter = run_iter
            return
        else:
            self._run_iter = 0

        with self.lock:
            if not self.active:
                return
            torcount = limiter.count
            if torcount > AUTOFUSE_RECENT_TOR_LIMIT_UPPER:
                # need tor cooldown, stop the waiting fusions
                for wallet, password in tuple(self.autofusing_wallets.items()):
                    with wallet.lock:
                        autofusions = set(wallet._fusions_auto)
                        for f in autofusions:
                            if f.status[0] in ('complete', 'failed'):
                                wallet._fusions_auto.discard(f)
                                continue
                            if not f.stopping:
                                f.stop('Tor cooldown', not_if_running = True)

            if torcount > AUTOFUSE_RECENT_TOR_LIMIT_LOWER:
                return
            for wallet, password in tuple(self.autofusing_wallets.items()):
                num_auto = 0
                with wallet.lock:
                    autofusions = set(wallet._fusions_auto)
                    for f in autofusions:
                        if f.status[0] in ('complete', 'failed'):
                            wallet._fusions_auto.discard(f)
                        else:
                            num_auto += 1
                    eligible, ineligible, sum_value, has_unconfirmed, has_coinbase = select_coins(wallet)
                    target_num_auto, confirmed_only = get_target_params_1(wallet, eligible)
                    #self.print_error("params1", target_num_auto, confirmed_only)
                    if num_auto < target_num_auto:
                        # we don't have enough auto-fusions running, so start one
                        if confirmed_only and has_unconfirmed:
                            for f in list(wallet._fusions_auto):
                                f.stop('Wallet has unconfirmed coins... waiting.', not_if_running = True)
                            continue
                        fraction = get_target_params_2(wallet, eligible, sum_value)
                        #self.print_error("params2", fraction)
                        coins = [c for l in select_random_coins(wallet, fraction, eligible) for c in l]
                        if not coins:
                            self.print_error("auto-fusion skipped due to lack of coins")
                            continue
                        try:
                            f = self.start_fusion(wallet, password, coins)
                            self.print_error("started auto-fusion")
                        except RuntimeError as e:
                            self.print_error(f"auto-fusion skipped due to error: {e}")
                            return
                        wallet._fusions_auto.add(f)
                    elif confirmed_only and has_unconfirmed:
                        for f in list(wallet._fusions_auto):
                            f.stop('Wallet has unconfirmed coins... waiting.', not_if_running = True)

    def start_fusion_server(self, network, bindhost, port, upnp = None):
        if self.fusion_server:
            raise RuntimeError("server already running")
        self.fusion_server = FusionServer(self.config, network, bindhost, port, upnp = upnp)
        self.fusion_server.start()
        return self.fusion_server.host, self.fusion_server.port

    def stop_fusion_server(self):
        try:
            self.fusion_server.stop('server stopped by operator')
            self.fusion_server = None
        except Exception:
            pass

    def update_coins_ui(self, wallet):
        ''' Default implementation does nothing. Qt plugin subclass overrides
        this, which sends a signal to the main thread to update the coins tab.
        This is called by the Fusion thread (in its thread context) when it
        freezes & unfreezes coins. '''

    def notify_server_status(self, b, tup : tuple = None):
        ''' The Qt plugin subclass implements this to tell the GUI about bad
        servers. '''
        if not b: self.print_error("notify_server_status:", b, str(tup))

    @daemon_command
    def fusion_server_start(self, daemon, config):
        # Usage:
        #   ./electron-cash daemon fusion_server_start <bindhost> <port>
        #   ./electron-cash daemon fusion_server_start <bindhost> <port> upnp
        network = daemon.network
        if not network:
            return "error: cannot run fusion server without an SPV server connection"
        def invoke(bindhost = '0.0.0.0', sport='8787', upnp_str = None):
            port = int(sport)
            pnp = get_upnp() if upnp_str == 'upnp' else None
            return self.start_fusion_server(network, bindhost, port, upnp = pnp)

        try:
            host, port = invoke(*config.get('subargs', ()))
        except Exception as e:
            import traceback, sys;  traceback.print_exc(file=sys.stderr)
            return f'error: {str(e)}'
        return (host, port)

    @daemon_command
    def fusion_server_stop(self, daemon, config):
        self.stop_fusion_server()
        return 'ok'

    @daemon_command
    def fusion_server_status(self, daemon, config):
        if not self.fusion_server:
            return "fusion server not running"
        return dict(poolsizes = {t: len(pool.pool) for t,pool in self.fusion_server.waiting_pools.items()})

    @daemon_command
    def fusion_server_fuse(self, daemon, config):
        if self.fusion_server is None:
            return
        subargs = config.get('subargs', ())
        if len(subargs) != 1:
            return "expecting tier"
        tier = int(subargs[0])
        num_clients = self.fusion_server.start_fuse(tier)
        return num_clients
