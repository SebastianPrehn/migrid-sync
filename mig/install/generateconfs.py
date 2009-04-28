#!/usr/bin/python
# -*- coding: utf-8 -*-
#
# --- BEGIN_HEADER ---
#
# generateconfs - create custom MiG server configuration files
# Copyright (C) 2003-2009  The MiG Project lead by Brian Vinter
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

# IMPORTANT: Run script with sudo or as root

"""Generate the configurations for a custom MiG server installation. Creates MiG server and Apache configurations to fit the provided user and path settings.
"""

import sys
import os
import re


def fill_template(template_file, output_file, dictionary):
    try:
        template = open(template_file, 'r')
        contents = template.read()
        template.close()
    except Exception, err:
        print 'Error: reading template file %s: %s' % (template_file,
                err)
        return False

    # print "template read:\n", output

    for (variable, value) in dictionary.items():
        contents = re.sub(variable, value, contents)

    # print "output:\n", contents

    # print "writing specific contents to %s" % (output_file)

    try:
        output = open(output_file, 'w')
        output.write(contents)
        output.close()
    except Exception, err:
        print 'Error: writing output file %s: %s' % (output_file, err)
        return False

    return True


def generate_confs(server_fqdn, user, group, apache_etc, apache_run, apache_log, mig_base, mig_state, mig_certs, http_port, https_port, source, destination):
    """Generate Apache and MiG server confs with specified variables"""
    mig_dir = os.path.join(mig_base, 'mig')

    user_dict = {}
    user_dict['__SERVER_FQDN__'] = server_fqdn
    user_dict['__USER__'] = user
    user_dict['__GROUP__'] = group
    user_dict['__HTTP_PORT__'] = str(http_port)
    user_dict['__HTTPS_PORT__'] = str(https_port)
    user_dict['__MIG_HOME__'] = mig_dir
    user_dict['__MIG_STATE__'] = mig_state
    user_dict['__MIG_CERTS__'] = mig_certs
    user_dict['__APACHE_ETC'] = apache_etc
    user_dict['__APACHE_RUN__'] = apache_run
    user_dict['__APACHE_LOG__'] = apache_log

    try:
        os.makedirs(destination)
    except OSError:
        pass
    
    apache_mig_template = os.path.join(source, 'apache-MiG-template.conf')
    apache_mig_conf = os.path.join(destination, 'MiG.conf')
    fill_template(apache_mig_template, apache_mig_conf, user_dict)
    apache_httpd_template = os.path.join(source, 'apache-httpd-template.conf')
    apache_httpd_conf = os.path.join(destination, 'httpd.conf')
    fill_template(apache_httpd_template, apache_httpd_conf, user_dict)
    apache_initd_template = os.path.join(source, 'apache-init.d-template')
    apache_initd_script = os.path.join(source, 'apache')
    fill_template(apache_initd_template, apache_initd_script, user_dict)
    os.chmod(apache_initd_script, 0755)

    server_template = os.path.join(source, 'MiGserver-template.conf')
    server_conf = os.path.join(destination, 'MiGserver.conf')
    fill_template(server_template, server_conf, user_dict)

    return True

if "__main__" == __name__:
    # ## Main ###

    if len(sys.argv) <= 10:
        print 'Usage: %s SERVER_FQDN USER GROUP APACHE_ETC APACHE_RUN APACHE_LOG MIG_BASE MIG_STATE MIG_CERTS HTTP_PORT HTTPS_PORT SOURCE DESTINATION' % sys.argv[0]
        sys.exit(1)

    names = ('server_fqdn', 'user', 'group', 'apache_etc', 'apache_run', 'apache_log', 'mig_base', 'mig_state', 'mig_certs', 'http_port', 'https_port', 'source', 'destination')
    values = tuple(sys.argv[1:14])
    pairs = zip(names, values)
    settings = dict(pairs)
    print settings
    print '''# Creating confs with:
server_fqdn: %(server_fqdn)s
user: %(user)s
group: %(group)s
apache_etc: %(apache_etc)s
apache_run: %(apache_run)s
apache_log: %(apache_log)s
mig_base: %(mig_base)s
mig_state: %(mig_state)s
mig_certs: %(mig_certs)s
http_port: %(http_port)s
https_port: %(https_port)s
source: %(source)s
destination: %(destination)s
''' % settings
    generate_confs(*values)

    sys.exit(0)
