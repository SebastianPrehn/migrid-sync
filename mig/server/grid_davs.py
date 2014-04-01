#!/usr/bin/python
# -*- coding: utf-8 -*-
#
# --- BEGIN_HEADER ---
#
# grid_davs - secure DAV server providing access to MiG user homes
# Copyright (C) 2003-2014  The MiG Project lead by Brian Vinter
#
# This file is part of MiG.
#
# MiG is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# MiG is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA  02110-1301, USA.
#
# -- END_HEADER ---
#

"""Provide secure DAV access to MiG user homes"""

import BaseHTTPServer
import SocketServer
import ssl
import os
import shutil
import sys
import urlparse

from pywebdav.server.fileauth import DAVAuthHandler
from pywebdav.server.fshandler import FilesystemHandler
#from pywebdav.server.daemonize import startstop
from pywebdav.lib.errors import DAV_NotFound

from shared.base import client_dir_id, client_alias, invisible_path
from shared.conf import get_configuration_object
from shared.griddaemons import get_fs_path, strip_root, \
     flags_to_mode, acceptable_chmod, refresh_users
from shared.useradm import check_password_hash


configuration, logger = None, None


class ThreadedHTTPServer(SocketServer.ThreadingMixIn,
                         BaseHTTPServer.HTTPServer):
    """Handle requests in a separate thread."""

    pass


def setup_dummy_config(**kw):
    """DAV config object helper"""

    class DummyConfigDAV:
        """Dummy DAV config"""
        def __init__(self, **kw):
            self.__dict__.update(**kw)

        def getboolean(self, name):
            """Get boolean value from config"""
            return (str(getattr(self, name, 0)) in ('1', "yes", "true", "on",
                                                    "True"))

    class DummyConfig:
        """Dummy config"""
        DAV = DummyConfigDAV(**kw)

    return DummyConfig()


def init_filesystem_handler(handler, directory, host, port, verbose):
    """Setup up file system handler to take data from user home"""

    dav_conf_dict = handler.server_conf.dav_cfg
    
    # dispatch directory and host to the filesystem handler
    # This handler is responsible from where to take the data
    handler.IFACE_CLASS = MiGFilesystemHandler(directory, 'http://%s:%s/' % \
                                               (host, port),
                                               handler.server_conf,
                                               handler._config, verbose)

    if not handler._config.DAV.getboolean('lockemulation'):
        logger.info('Deactivated LOCK, UNLOCK (WebDAV level 2) support')

    handler.IFACE_CLASS.mimecheck = True
    if not handler._config.DAV.getboolean('mimecheck'):
        handler.IFACE_CLASS.mimecheck = False
        logger.info('Disabled mimetype sniffing (All files will have type '
                    'application/octet-stream)')

    if dav_conf_dict['baseurl']:
        logger.info('Using %(baseurl)s as base url for PROPFIND requests' % \
                     dav_conf_dict)
    handler.IFACE_CLASS.baseurl = dav_conf_dict['baseurl']


class MiGFilesystemHandler(FilesystemHandler):
    """
    Overrides the default FilesystemHandler to include chroot support and
    hidden files like in other MiG file interfaces.
    """

    def __init__(self, directory, uri, server_conf, dav_conf, verbose=False):
        """Simply call parent constructor"""
        FilesystemHandler.__init__(self, directory, uri, verbose)
        self.logger = logger
        self.root = directory
        self.daemon_conf = server_conf.daemon_conf
        self.chroot_exceptions = self.daemon_conf['chroot_exceptions']
        self.chmod_exceptions = self.daemon_conf['chmod_exceptions']

    # Use shared daemon fs helper functions
    
    def _get_fs_path(self, davs_path):
        """Wrap helper"""
        self.logger.debug("get_fs_path: %s" % davs_path)
        reply = get_fs_path(davs_path, self.root, self.chroot_exceptions)
        self.logger.debug("get_fs_path returns: %s :: %s" % (davs_path,
                                                             reply))
        return reply

    def _strip_root(self, davs_path):
        """Wrap helper"""
        self.logger.debug("strip_root: %s" % davs_path)
        reply = strip_root(davs_path, self.root, self.chroot_exceptions)
        self.logger.debug("strip_root returns: %s :: %s" % (davs_path,
                                                             reply))
        return reply
    
    def _acceptable_chmod(self, davs_path, mode):
        """Wrap helper"""
        self.logger.debug("acceptable_chmod: %s" % davs_path)
        reply = acceptable_chmod(davs_path, mode, self.chmod_exceptions)
        self.logger.debug("acceptable_chmod returns: %s :: %s" % (davs_path,
                                                                  reply))
        return reply

    def uri2local(self, uri):
        """map uri in baseuri and local part"""

        uparts = urlparse.urlparse(uri)
        fileloc = uparts[2][1:]
        rel_path = os.path.join(fileloc)
        try:
            filename = self._get_fs_path(rel_path)
        except ValueError, vae:
            self.logger.warning("illegal path requested: %s :: %s" % (rel_path,
                                                                      vae))
            raise DAV_NotFound
        return filename

    def get_childs(self, uri, filter=None):
        """return the child objects as self.baseuris for the given URI.
        We override the listing to hide invisible_path hits.
        """
        
        fileloc = self.uri2local(uri)
        filelist = []
        
        if os.path.exists(fileloc):
            if os.path.isdir(fileloc):
                try:
                    files = os.listdir(fileloc)
                except:
                    raise DAV_NotFound
                
                for filename in files:
                    if invisible_path(filename):
                        continue
                    newloc = os.path.join(fileloc, filename)
                    filelist.append(self.local2uri(newloc))
                    
                    self.logger.info('get_childs: Childs %s' % filelist)
                    
        return filelist
                

class MiGDAVAuthHandler(DAVAuthHandler):
    """
    Provides MiG specific authentication based on parameters. The calling
    class has to inject password and username into this.
    (Variables: auth_user and auth_pass)

    Override simple static user/password auth with a simple password lookup in
    the MiG user DB.
    """

    # TODO: add pubkey auth

    # Do not forget to set IFACE_CLASS by caller
    # ex.: IFACE_CLASS = FilesystemHandler('/tmp', 'http://localhost/')
    verbose = False
    users = None
    authenticated_user = None

    def _log(self, message):
        print "in _log"
        if self.verbose:
            logger.info(message)

    def _check_auth_password(self, username, password):
        """Verify supplied username and password against user DB"""
        offered = None
        if self.users.has_key(username):
            # list of User login objects for username
            entries = self.users[username]
            offered = password
            for entry in entries:
                if entry.password is not None:
                    allowed = entry.password
                    logger.debug("Password check for %s" % username)
                    if check_password_hash(offered, allowed):
                        self.authenticated_user = username
                        return True
        return False


    def _check_auth_publickey(self, username, key):
        offered = None
        if self.users.has_key(username):
            # list of User login objects for username
            entries = self.users[username]
            offered = key.get_base64()
            for entry in entries:
                if entry.public_key is not None:
                    allowed = entry.public_key.get_base64()
                    self.logger.debug("Public key check for %s" % username)
                    if allowed == offered:
                        self.logger.info("Public key match for %s" % username)
                        self.authenticated_user = username
                        return paramiko.AUTH_SUCCESSFUL

    def _chroot_user(self, username, host, port, verbose):
        """Swith to user home"""
        # list of User login objects for user_name
        entries = self.users[self.authenticated_user]
        for entry in entries:
            if entry.chroot:
                directory = os.path.join(self.server_conf.user_home,
                                         entry.home)
                logger.info("switching to user home %s" % directory)
                init_filesystem_handler(self, directory, host, port, verbose)
                return
        logger.info("leaving root directory alone")
        

    def send_body(self, DATA, code=None, msg=None, desc=None,
                  ctype='application/octet-stream', headers={}):
        """Override default send_body method of DAVRequestHandler and thus
        DAVAuthHandler:
        For some silly reason pywebdav somtimes calls send_body with str code
        but back-end send_response from BaseHTTPServer.py expects int. Foce
        conversion if needed.
        Without this fix locking/writing of files fails with mapped network
        drives on Windows.
        """
        if isinstance(code, basestring) and code.isdigit():
            code = int(code)
                                               
        DAVAuthHandler.send_body(self, DATA, code, msg, desc, ctype, headers)
        
    def get_userinfo(self, username, password, command):
        """Authenticate user against user DB. Returns 1 on success and None
        otherwise.
        """

        refresh_users(configuration, 'davs')
        usermap = {}
        for user_obj in self.server_conf.daemon_conf['users']:
            if not usermap.has_key(user_obj.username):
                usermap[user_obj.username] = []
            usermap[user_obj.username].append(user_obj)
        self.users = usermap
        logger.debug("get_userinfo found users: %s" % self.users)

        host = self.server_conf.user_davs_address.strip()
        port = self.server_conf.user_davs_port
        verbose = self._config.DAV.getboolean('verbose')

        if 'password' in self.server_conf.user_davs_auth and \
                 self._check_auth_password(username, password):
            logger.info("Authenticated %s" % username)
            # dispatch directory and host to the filesystem handler
            # responsible for deciding where to take the data from
            self._chroot_user(username, host, port, verbose)
            return 1
        else:
            err_msg = "Password authentication failed for %s" % username
            logger.error(err_msg)
            print err_msg
        return None
            
            
            
def run(configuration):
    """SSL wrap HTTP server for secure DAV access"""

    handler = MiGDAVAuthHandler

    # Force AuthRequestHandler to HTTP/1.1 to allow persistent connections

    handler.protocol_version = 'HTTP/1.1'

    # Pass conf options to DAV handler in required object format

    dav_conf_dict = configuration.dav_cfg
    dav_conf_dict['host'] = configuration.user_davs_address
    dav_conf_dict['port'] = configuration.user_davs_port
    dav_conf = setup_dummy_config(**dav_conf_dict)
    # inject options
    handler.server_conf = configuration
    handler._config = dav_conf

    server = ThreadedHTTPServer

    directory = dav_conf_dict['directory'].strip().rstrip('/')
    verbose = dav_conf.DAV.getboolean('verbose')
    noauth = dav_conf.DAV.getboolean('noauth')
    nossl = dav_conf.DAV.getboolean('nossl')
    host = dav_conf_dict['host'].strip()
    port = dav_conf_dict['port']

    if not os.path.isdir(directory):
        logger.error('%s is not a valid directory!' % directory)
        return sys.exit(233)

    # basic checks against wrong hosts
    if host.find('/') != -1 or host.find(':') != -1:
        logger.error('Malformed host %s' % host)
        return sys.exit(233)

    # no root directory
    if directory == '/':
        logger.error('Root directory not allowed!')
        sys.exit(233)

    # put some extra vars
    handler.verbose = verbose
    if noauth:
        logger.warning('Authentication disabled!')
        handler.DO_AUTH = False

    logger.info('Serving data from %s' % directory)

    init_filesystem_handler(handler, directory, host, port, verbose)

    # initialize server on specified port
    runner = server((host, port), handler)

    # Wrap in SSL if enabled
    if nossl:
        logger.warning('Not wrapping connections in SSL - only for testing!')
    else:
        cert_path = configuration.user_davs_key
        if not os.path.isfile(cert_path):
            logger.error('No such server key: %s' % cert_path)
            sys.exit(1)
        logger.info('Wrapping connections in SSL')
        runner.socket = ssl.wrap_socket(runner.socket,
                                        certfile=cert_path,
                                        server_side=True)
        
    print('Listening on %s (%i)' % (host, port))

    try:
        runner.serve_forever()
    except KeyboardInterrupt:
        logger.info('Killed by user')


if __name__ == "__main__":
    configuration = get_configuration_object()
    logger = configuration.logger
    configuration.dav_cfg = {
               'verbose': False,
               'directory': configuration.user_home,
               'no_auth': False,
               'user': '',
               'password': '',
               'daemonize': False,
               'daemonaction': 'start',
               'counter': 0,
               'mysql': False,
               'lockemulation': True,
               'http_response_use_iterator':  True,
               'chunked_http_response': True,
               'mimecheck': True,
               'baseurl': '',
               'nossl': False,
        }

    # TMP: separate logger for now
    #logger = configuration.logger
    import logging
    logging.basicConfig(filename="davs.log", level=logging.DEBUG,
                        format="%(asctime)s %(levelname)s %(message)s")
    logger = logging
    if not configuration.site_enable_davs:
        err_msg = "DAVS access to user homes is disabled in configuration!"
        logger.error(err_msg)
        print err_msg
        sys.exit(1)

    chroot_exceptions = [os.path.abspath(configuration.vgrid_private_base),
                         os.path.abspath(configuration.vgrid_public_base),
                         os.path.abspath(configuration.vgrid_files_home),
                         os.path.abspath(configuration.resource_home)]
    # Don't allow chmod in dirs with CGI access as it introduces arbitrary
    # code execution vulnerabilities
    chmod_exceptions = [os.path.abspath(configuration.vgrid_private_base),
                         os.path.abspath(configuration.vgrid_public_base)]
    configuration.daemon_conf = {
        'address': configuration.user_davs_address,
        'port': configuration.user_davs_port,
        'root_dir': os.path.abspath(configuration.user_home),
        'chmod_exceptions': chmod_exceptions,
        'chroot_exceptions': chroot_exceptions,
        'allow_password': 'password' in configuration.user_davs_auth,
        'allow_publickey': 'publickey' in configuration.user_davs_auth,
        'user_alias': configuration.user_davs_alias,
        'users': [],
        'time_stamp': 0,
        'logger': logger,
        }

    print """
Running grid davs server for user dav access to their MiG homes.

Set the MIG_CONF environment to the server configuration path
unless it is available in mig/server/MiGserver.conf
"""
    logger.info("starting DAV server")
    try:
        run(configuration)
    except KeyboardInterrupt:
        logger.info("received interrupt - shutting down")
    except Exception, exc:
        logger.error("exiting on unexpected exception: %s" % exc)
