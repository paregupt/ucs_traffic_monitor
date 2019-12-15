#! /usr/bin/python3

__author__ = "Paresh Gupta"
__version__ = "0.2"

import sys
import os
import argparse
import logging
from logging.handlers import RotatingFileHandler
import pickle
import json
import time
from collections import Counter
from ucsmsdk.ucshandle import UcsHandle
from netmiko import ConnectHandler
import concurrent.futures

"""
# No support for RACK-MOUNT
if 'rack-unit' in item.dn:
    return
"""

HOURS_IN_DAY = 24
MINUTES_IN_HOUR = 60
SECONDS_IN_MINUTE = 60
# Default UCS session timeout is 7200s (120m). Logout and login proactively
# every 5400s (90m)
CONNECTION_REFRESH_INTERVAL = 5400

user_args = {}

FILENAME_PREFIX = __file__.replace('.py', '')
INPUT_FILE_PREFIX = ''
CONNECTION_TIMEOUT = 10

LOGFILE_LOCATION = '/var/log/telegraf/'
LOGFILE_SIZE = 10000000
LOGFILE_NUMBER = 5
logger = logging.getLogger('ucs_stats_pull')

# Dictionary with key as IP and value as list of user and passwd
domain_dict = {}
# Dictionary with key as IP and value as a dictionary of type and handle.
# handle is netmiko.ConnectHandler when type is 'cli'
# handle is UcsHandle when type is 'sdk'
conn_dict = {}

# This dictionary is populated with connections from a pickle file updated on
# previous execution
pickled_connections = {}

# Stats for all FI, chassis, blades, etc. are collected here before printing
# in the desired output format
stats_dict = {}

# Used to store objects returned by the stats pull. These must be processed
# to update stats_dict
raw_cli_stats = {}
raw_sdk_stats = {}

# List of class IDs to be pulled from UCS
class_ids = ['TopSystem',
             'NetworkElement',
             'SwSystemStats',
             'FcPIo',
             'FabricFcSanPc',
             'FabricFcSanPcEp',
             'FcStats',
             'FcErrStats',
             'EtherPIo',
             'FabricEthLanPc',
             'FabricEthLanPcEp',
             'EtherRxStats',
             'EtherTxStats',
             'EtherErrStats',
             'EtherLossStats',
             'FabricDceSwSrvPc',
             'FabricDceSwSrvPcEp',
             'AdaptorVnicStats',
             'AdaptorHostEthIf',
             'AdaptorHostFcIf',
             'DcxVc',
             'EtherServerIntFIo',
             'EtherServerIntFIoPc',
             'EtherServerIntFIoPcEp',
             'FabricPathEp',
             'ComputeBlade'
            ]


def parse_cmdline_arguments():
    desc_str = \
    'Pull stats from Cisco UCS domain and print output in different formats \n' + \
    'like InfluxDB Line protocol'
    epilog_str = \
    'This file pulls stats from Cisco UCS and convert it into a database\n' + \
    'insert format. The database can be used by a front-end like Grafana.\n' + \
    'The initial version was coded to insert into InfluxDB but can be\n' + \
    'extended for other databases also.\n\n' + \
    'High level steps:\n' + \
    '  - Read access details of a Cisco UCS domain (IP Address, user\n' + \
    '    (read-only is enough) and password) from the input file\n' + \
    '  - Use UCSM SDK (https://github.com/CiscoUcs/ucsmsdk) to pull stats\n'+ \
    '  - Stats which are unavailable via above, SSH to UCS and parse the\n' + \
    '    command output. Use Netmiko (https://github.com/ktbyers/netmiko)\n' + \
    '  - Stitch the output for end-to-end traffic mapping (like the\n' + \
    '    uplink port used by blade vNIC/vHBA) and store in a dictionary\n' + \
    '  - Finally, read the dictionary content to print in the desired\n' + \
    '    output format, like InfluxDB Line Protocol'

    parser = argparse.ArgumentParser(description=desc_str, epilog=epilog_str,
                formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('input_file', action='store', help='file containing \
                    the UCS domain information in the format: IP,user,password')
    parser.add_argument('output_format', action='store', help='specify the \
                    output format', choices=['dict', 'influxdb-lp'])
    parser.add_argument('-V', '--verify-only', dest='verify_only', \
                    action='store_true', default=False, help='verify \
                    connection and stats pull but do not print the stats')
    parser.add_argument('-dss', dest='dont_save_sessions', \
                    action='store_true', default=False, help='don\'t save \
                    sessions (dss). By default, UCS sessions (SDK only, not \
                    SSH) are saved (using Python pickle) for re-use when this \
                    program is executed every few seconds.')
    parser.add_argument('-v', '--verbose', dest='verbose', \
                    action='store_true', default=False, help='warn and above')
    parser.add_argument('-vv', '--more_verbose', dest='more_verbose', \
                    action='store_true', default=False, help='info and above')
    parser.add_argument('-vvv', '--most_verbose', dest='most_verbose', \
                    action='store_true', default=False, help='debug and above')
    args = parser.parse_args()
    user_args['input_file'] = args.input_file
    user_args['verify_only'] = args.verify_only
    user_args['output_format'] = args.output_format
    user_args['verbose'] = args.verbose
    user_args['more_verbose'] = args.more_verbose
    user_args['most_verbose'] = args.most_verbose
    user_args['dont_save_sessions'] = args.dont_save_sessions

    global INPUT_FILE_PREFIX
    INPUT_FILE_PREFIX = ((((user_args['input_file']).split('/'))[-1]).split('.'))[0]

def pre_checks_passed(argv):
    if sys.version_info[0] < 3:
        print('Unsupported with Python 2. Must use Python 3')
        return False
    if (len(argv) <= 1):
        print('Try -h option for usage help')
        return False

    return True

def setup_logging():
    this_filename = (FILENAME_PREFIX.split('/'))[-1]
    logfile_location = LOGFILE_LOCATION + this_filename
    logfile_prefix = logfile_location + '/' + this_filename
    try:
        os.mkdir(logfile_location)
    except FileExistsError:
        pass
    except Exception as e:
        # Log in local directory if can't be created in LOGFILE_LOCATION
        logfile_prefix = FILENAME_PREFIX
    finally:
        logfile_name = logfile_prefix + '_' + INPUT_FILE_PREFIX + '.log'
        rotator = RotatingFileHandler(logfile_name, maxBytes=LOGFILE_SIZE,
                                      backupCount=LOGFILE_NUMBER)
        formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
        rotator.setFormatter(formatter)
        logger.addHandler(rotator)

        if user_args.get('verbose'):
            logger.setLevel(logging.WARNING)
        if user_args.get('more_verbose'):
            logger.setLevel(logging.INFO)
        if user_args.get('most_verbose'):
            logger.setLevel(logging.DEBUG)

def get_ucs_domains():
    """
    Parse the --input-file argument to get UCS domain(s)

    The format of the file is expected to carry a list as:
    <IP Address 1>,username 1,password 1
    <IP Address 2>,username 2,password 2
    Only one entry is expected per line. Line with prefix # is ignored
    Location is specified between []
    Initialize stats_dict for valid UCS domain

    Parameters:
    None

    Returns:
    None

    """

    global domain_dict
    location = ''
    input_file = user_args['input_file']
    with open(input_file, 'r') as f:
        for line in f:
            if not line.startswith('#'):
                line = line.strip()
                if line.startswith('['):
                    if not line.endswith(']'):
                        logger.error('Input file {} format error. Line starts' \
                        ' with [ but does not end with ]: {}\nExiting...' \
                        .format(input_file, line))
                        sys.exit()
                    line = line.replace('[', '')
                    line = line.replace(']', '')
                    line = line.strip()
                    location = line
                    continue

                domain = line.split(',')
                domain_dict[domain[0]] = [domain[1], domain[2]]
                logger.info('Added {} to domain dict'.format(domain[0]))
                stats_dict[domain[0]] = {}
                stats_dict[domain[0]]['location'] = location
                stats_dict[domain[0]]['A'] = {}
                stats_dict[domain[0]]['A']['fi_ports'] = {}
                stats_dict[domain[0]]['B'] = {}
                stats_dict[domain[0]]['B']['fi_ports'] = {}
                stats_dict[domain[0]]['chassis'] = {}

                conn_dict[domain[0]] = {}

    if not domain_dict:
        logger.warning('No UCS domains to monitor. Check input file. Exiting.')
        sys.exit()

'''
InfluxDB Line Protocol Reference
* Never double or single quote the timestamp
* Never single quote field values
* Do not double or single quote measurement names, tag keys, tag values,
    and field keys
* Do not double quote field values that are floats, integers, or Booleans
* Do double quote field values that are strings
'''
def print_output_in_influxdb_lp():
    global stats_dict
    final_print_string = ''
    fi_id_list = ['A', 'B']

    blade_servers_prefix = 'BladeServers,domain='
    bp_port_prefix = 'BackplanePortStats,domain='
    vnic_prefix = 'VnicStats,domain='
    fi_server_port_prefix = 'FIServerPortStats,domain='
    fi_uplink_port_prefix = 'FIUplinkPortStats,domain='

    for domain_ip, d_dict in stats_dict.items():
        if 'mode' not in d_dict:
            logger.warning('Unable to print InfluxDB Line Protocol for {}' \
                            .format(domain_ip))
            logger.debug('d_dict : \n {}'.format(json.dumps(d_dict, indent=2)))
            continue
        location = d_dict['location']
        mode = d_dict['mode']
        name = d_dict['name']
        uptime = d_dict['uptime']
        for fi_id in fi_id_list:
            fi_dict = d_dict[fi_id]

            # Build insert string for FIEnvStats
            fi_env_prefix = 'FIEnvStats,domain='
            fi_env_tags = ','
            fi_env_fields = ' '
            fi_env_prefix = fi_env_prefix + domain_ip
            fi_env_tags = fi_env_tags + 'fi_id=' + fi_id + ',location=' + \
                            location
            fi_env_fields = fi_env_fields + 'load=' + fi_dict['load'] + \
                            ',total_memory=' + fi_dict['total_memory'] + \
                            ',mem_available=' + fi_dict['mem_available'] + \
                            ',model="' + fi_dict['model'] + '"' + \
                            ',serial="' + fi_dict['serial'] + '"' + \
                            ',oob_if_ip="' + fi_dict['oob_if_ip'] + '"' + \
                            ',mode="' + mode + '"' + ',name="' + name + '"' + \
                            ',sys_uptime=' + (str)(uptime)
            fi_env_fields = fi_env_fields + '\n'
            final_print_string = final_print_string + fi_env_prefix + \
                                        fi_env_tags + fi_env_fields
            # Done: Build insert string for FIEnvStats

            # Build insert string for FIServerPortStats and FIUplinkPortStats
            '''
            The per_fi_port_dict can have different keys and may require
            multiple if-checks. This logic is coded but commented below.
            Checks are:
            - Only server ports can be to_chassis, to_iom_slot, to_iom_port
            - Only Eth ports can have pause stats
            - PC do not have pause stats
            - Only uplink PC have tx and rx bytes. Server PC do not have
            - etc.
            The other logic can be to just check for the existence of a key
            before printing it. This logic is also coded below
            '''

            fi_port_dict = fi_dict['fi_ports']
            for fi_port, per_fi_port_dict in fi_port_dict.items():
                fi_port_tags = ','
                fi_port_fields = ' '
                fi_port_tags = fi_port_tags + 'fi_id=' + fi_id + \
                            ',port=' + fi_port + \
                            ',transport=' + per_fi_port_dict['transport'] + \
                            ',location=' + location

                fi_port_fields = fi_port_fields + \
                'admin_state="' + per_fi_port_dict['admin_state'] + '",' + \
                'description="' + per_fi_port_dict['name'] + '",' + \
                'oper_speed=' + (str)(per_fi_port_dict['oper_speed']) + ',' + \
                'oper_state="' + (str)(per_fi_port_dict['oper_state']) + '"'

                if 'channel' in per_fi_port_dict:
                    fi_port_tags = fi_port_tags + ',channel=' + \
                                    per_fi_port_dict['channel']
                if 'to_chassis' in per_fi_port_dict:
                    fi_port_tags = fi_port_tags + ',to_chassis=' + \
                                    per_fi_port_dict['to_chassis']
                if 'to_iom_slot' in per_fi_port_dict:
                    fi_port_tags = fi_port_tags + ',to_iom_slot=' + \
                                    per_fi_port_dict['to_iom_slot']
                if 'to_iom_port' in per_fi_port_dict:
                    fi_port_tags = fi_port_tags + ',to_iom_port=' + \
                                    per_fi_port_dict['to_iom_port']
                if 'bytes_rx_delta' in per_fi_port_dict:
                    fi_port_fields = fi_port_fields + ',bytes_rx_delta=' + \
                                    per_fi_port_dict['bytes_rx_delta']
                if 'bytes_tx_delta' in per_fi_port_dict:
                    fi_port_fields = fi_port_fields + ',bytes_tx_delta=' + \
                                    per_fi_port_dict['bytes_tx_delta']
                if 'crc_rx_delta' in per_fi_port_dict:
                    fi_port_fields = fi_port_fields + ',crc_rx_delta=' + \
                                    per_fi_port_dict['crc_rx_delta']
                if 'discard_rx_delta' in per_fi_port_dict:
                    fi_port_fields = fi_port_fields + ',discard_rx_delta=' + \
                                    per_fi_port_dict['discard_rx_delta']
                if 'discard_tx_delta' in per_fi_port_dict:
                    fi_port_fields = fi_port_fields + ',discard_tx_delta=' + \
                                    per_fi_port_dict['discard_tx_delta']
                if 'link_failures_delta' in per_fi_port_dict:
                    fi_port_fields = fi_port_fields + ',link_failures_delta='+\
                                    per_fi_port_dict['link_failures_delta']
                if 'pause_rx' in per_fi_port_dict:
                    fi_port_fields = fi_port_fields + ',pause_rx=' + \
                                    per_fi_port_dict['pause_rx']
                if 'pause_tx' in per_fi_port_dict:
                    fi_port_fields = fi_port_fields + ',pause_tx=' + \
                                    per_fi_port_dict['pause_tx']
                if 'sync_losses_delta' in per_fi_port_dict:
                    fi_port_fields = fi_port_fields + ',sync_losses_delta=' + \
                                    per_fi_port_dict['sync_losses_delta']
                if 'signal_losses_delta' in per_fi_port_dict:
                    fi_port_fields = fi_port_fields + ',signal_losses_delta='+\
                                    per_fi_port_dict['signal_losses_delta']
                # Ports will role server goes in FIServerPortStats, rest all
                # ports go into FIUplinkPortStats, including unknown
                if per_fi_port_dict['if_role'] == 'server':
                    fi_port_prefix = fi_server_port_prefix
                else:
                    fi_port_prefix = fi_uplink_port_prefix
                '''
                if 'PC' not in fi_port:
                    fi_port_tags = fi_port_tags + ',channel=' + \
                                        per_fi_port_dict['channel']

                # Ports will role server goes in FIServerPortStats, rest all
                # ports go into FIUplinkPortStats, including unknown
                if per_fi_port_dict['if_role'] == 'server':
                    fi_port_prefix = fi_server_port_prefix
                    if 'PC' not in fi_port:
                        fi_port_tags = fi_port_tags + ',to_chassis=' + \
                                    per_fi_port_dict['to_chassis'] + \
                                    ',to_iom_slot=' + \
                                        per_fi_port_dict['to_iom_slot'] + \
                                    ',to_iom_port=' + \
                                        per_fi_port_dict['to_iom_port']
                        fi_port_fields = fi_port_fields + \
                        'bytes_rx_delta=' + \
                                per_fi_port_dict['bytes_rx_delta'] + ',' + \
                        'bytes_tx_delta=' + \
                                per_fi_port_dict['bytes_tx_delta'] + ',' + \
                        'pause_rx=' + per_fi_port_dict['pause_rx'] + ',' + \
                        'pause_tx=' + per_fi_port_dict['pause_tx']
                else:
                    fi_port_prefix = fi_uplink_port_prefix
                    fi_port_fields = fi_port_fields + \
                    'bytes_rx_delta=' + \
                            per_fi_port_dict['bytes_rx_delta'] + ',' + \
                    'bytes_tx_delta=' + \
                            per_fi_port_dict['bytes_tx_delta']
                    if 'pause_rx' in per_fi_port_dict:
                        fi_port_fields = fi_port_fields + \
                        ',pause_rx=' + per_fi_port_dict['pause_rx'] + ',' + \
                        'pause_tx=' + per_fi_port_dict['pause_tx']
                '''
                fi_port_prefix = fi_port_prefix + domain_ip
                fi_port_fields = fi_port_fields + '\n'
                final_print_string = final_print_string + fi_port_prefix + \
                                        fi_port_tags + fi_port_fields
            # Done: Build insert string FIServerPortStats and FIUplinkPortStats

        # Now to Vnic, Backplane, compute blades, etc.
        chassis_dict = d_dict['chassis']
        for chassis_id, per_chassis_dict in chassis_dict.items():
            blade_dict = per_chassis_dict['blades']
            for blade_id, per_blade_dict in blade_dict.items():
                # Build insert string for BladeServers
                blade_prefix = blade_servers_prefix + domain_ip
                blade_tags = ','
                blade_fields = ' '
                blade_tags = blade_tags + 'service_profile=' + \
                                per_blade_dict['service_profile'] + \
                            ',location=' + location + \
                            ',chassis=' + chassis_id + \
                            ',blade=' + blade_id

                blade_fields = blade_fields + \
                    'admin_state="' + per_blade_dict['admin_state'] + '"' + \
                    ',association="' + per_blade_dict['association'] + '"' + \
                    ',operability="' + per_blade_dict['operability'] + '"' + \
                    ',oper_state="' + per_blade_dict['oper_state'] + '"' + \
                    ',oper_state_code=' + \
                            (str)(per_blade_dict['oper_state_code']) + \
                    ',memory=' + per_blade_dict['memory'] + \
                    ',model="' + per_blade_dict['model'] + '"' + \
                    ',num_adaptors=' + per_blade_dict['num_adaptors'] + \
                    ',num_cores=' + per_blade_dict['num_cores'] + \
                    ',num_cpus=' + per_blade_dict['num_cpus'] + \
                    ',num_vEths=' + per_blade_dict['num_vEths'] + \
                    ',num_vFCs=' + per_blade_dict['num_vFCs'] + \
                    ',serial="' + per_blade_dict['serial'] + '"'

                blade_fields = blade_fields + '\n'
                final_print_string = final_print_string + \
                    blade_prefix + blade_tags + blade_fields
                # Done: Build insert string for BladeServers

                # This is a strict check before going any deeper. Candidate
                # for re-visit. If this check is removed, check the presence
                # of keys in VnicState before filling in the values because
                # keys may be missing like fi_id, uplink_port, etc.
                if 'ok' not in per_blade_dict['oper_state'] or \
                    'associated' not in per_blade_dict['association']:
                    continue

                # Build insert string for VnicStats
                adaptor_dict = per_blade_dict['adaptors']
                for adaptor_id, per_adaptor_dict in adaptor_dict.items():
                    vif_dict = per_adaptor_dict['vifs']
                    for vif_name, per_vif_dict in vif_dict.items():
                        if 'up' not in per_vif_dict['link_state']:
                            continue
                        v_prefix = vnic_prefix + domain_ip
                        vnic_tags = ','
                        vnic_fields = ' '
                        vnic_tags = vnic_tags + 'adaptor=' + adaptor_id + \
                            ',blade=' + blade_id + ',chassis=' + chassis_id + \
                            ',domain_name=' + name
                        if 'pinned_fi_id' in per_vif_dict:
                            vnic_tags = vnic_tags + ',fi_id=' + \
                                            per_vif_dict['pinned_fi_id']
                        vnic_tags = vnic_tags + ',iom_backplane_port=' + \
                                        per_vif_dict['iom_backplane_port']
                        if 'pinned_fi_uplink' in per_vif_dict:
                            vnic_tags = vnic_tags + ',pinned_uplink=' + \
                                per_vif_dict['pinned_fi_uplink']
                        vnic_tags = vnic_tags + ',service_profile=' + \
                                per_blade_dict['service_profile'] + \
                            ',transport=' + per_vif_dict['transport'] + \
                            ',vif_name=' + vif_name + \
                            ',location=' + location

                        if 'bound_vfc' in per_vif_dict:
                            vnic_tags = vnic_tags + \
                                ',bound_vfc=' + per_vif_dict['bound_vfc']
                        if 'bound_veth' in per_vif_dict:
                            vnic_tags = vnic_tags + \
                                ',bound_veth=' + per_vif_dict['bound_veth']

                        vnic_fields = vnic_fields + \
                        'bytes_rx_delta=' + per_vif_dict['bytes_rx_delta'] + \
                        ',bytes_tx_delta=' + per_vif_dict['bytes_tx_delta'] + \
                        ',errors_rx_delta='+per_vif_dict['errors_rx_delta'] + \
                        ',errors_tx_delta='+per_vif_dict['errors_tx_delta'] + \
                        ',dropped_rx_delta='+per_vif_dict['dropped_rx_delta']+\
                        ',dropped_tx_delta='+per_vif_dict['dropped_tx_delta']

                        vnic_fields = vnic_fields + '\n'
                        final_print_string = final_print_string + v_prefix \
                                                + vnic_tags + vnic_fields
                # Done: Build insert string for VnicStats

            # Build insert string for BackplanePortStats
            if 'bp_ports' not in per_chassis_dict:
                continue
            bp_port_dict = per_chassis_dict['bp_ports']
            blade_dict = per_chassis_dict['blades']
            for iom_slot_id, iom_slot_dict in bp_port_dict.items():
                for bp_port_id, per_bp_port_dict in iom_slot_dict.items():
                    bp_prefix = bp_port_prefix + domain_ip
                    bp_tags = ','
                    bp_fields = ' '
                    bp_tags = bp_tags + 'bp_port=' + iom_slot_id + '/' + \
                        bp_port_id + ',chassis=' + chassis_id + \
                        ',fi_id=' + per_bp_port_dict['fi_id']
                    if 'adaptor_port' in per_bp_port_dict:
                        bp_tags = bp_tags + ',adaptor_port=' + \
                                    per_bp_port_dict['adaptor_port']
                    if 'channel' in per_bp_port_dict:
                        bp_tags = bp_tags + ',channel=' + \
                                    per_bp_port_dict['channel']
                    if 'fi_server_port' in per_bp_port_dict:
                        if per_bp_port_dict['fi_server_port'] == '':
                            bp_tags = bp_tags + ',fi_server_port=unknown'
                        else:
                            bp_tags = bp_tags + ',fi_server_port=' + \
                                    per_bp_port_dict['fi_server_port']
                    if 'to_adaptor' in per_bp_port_dict:
                        bp_tags = bp_tags + ',to_adaptor=' + \
                                    per_bp_port_dict['to_adaptor']
                    if 'to_blade' in per_bp_port_dict:
                        bp_tags = bp_tags + ',to_blade=' + \
                                    per_bp_port_dict['to_blade']
                        per_blade_dict = blade_dict[per_bp_port_dict['to_blade']]
                        bp_tags = bp_tags + ',to_service_profile=' + \
                                    per_blade_dict['service_profile']

                    if 'oper_speed' in per_bp_port_dict:
                        bp_fields = bp_fields + 'speed=' + \
                                    (str)(per_bp_port_dict['oper_speed'])
                    if 'admin_speed' in per_bp_port_dict:
                        bp_fields = bp_fields + 'speed=' + \
                                    (str)(per_bp_port_dict['admin_speed'])
                    if 'bytes_rx_delta' in per_bp_port_dict:
                        bp_fields = bp_fields + ',bytes_rx_delta=' + \
                                    per_bp_port_dict['bytes_rx_delta']
                    if 'bytes_tx_delta' in per_bp_port_dict:
                        bp_fields = bp_fields + ',bytes_tx_delta=' + \
                                    per_bp_port_dict['bytes_tx_delta']
                    if 'oper_state' in per_bp_port_dict:
                        bp_fields = bp_fields + ',oper_state="' + \
                                    per_bp_port_dict['oper_state'] + '"'
                    if 'pause_rx' in per_bp_port_dict:
                        bp_fields = bp_fields + ',pause_rx=' + \
                                    per_bp_port_dict['pause_rx']
                    if 'pause_tx' in per_bp_port_dict:
                        bp_fields = bp_fields + ',pause_tx=' + \
                                    per_bp_port_dict['pause_tx']

                    bp_fields = bp_fields + '\n'
                    final_print_string = final_print_string + bp_prefix \
                                            + bp_tags + bp_fields
            # Done: Build insert string for BackplanePortStats
    print(final_print_string)

def print_output():
    if user_args['verify_only']:
        logger.info('Skipping output in {} due to -V option' \
                    .format(user_args['output_format']))
        return
    if user_args['output_format'] == 'dict':
        #print(json.dumps(stats_dict, indent=2))
        logger.debug('stats_dict : \n {}'.format(json.dumps(stats_dict, indent=2)))
    elif user_args['output_format'] == 'influxdb-lp':
        print_output_in_influxdb_lp()
    else:
        logger.error('Unknown output format type: {}'. \
        format(user_args['output_format']))

def set_ucs_connection(domain_ip, conn_type):
    """
    Given IP Address of UCS domain, allocate a new connection handle and
    login to the UCS domain

    Must be multithreading aware.

    Parameters:
    domain_ip (IP Address of UCS domain)
    conn_type (cli or sdk)

    Returns:
    handle (UcsHandle or netmiko.ConnectHandler)

    """

    global domain_dict
    handle = None
    if domain_ip in domain_dict:
        user = domain_dict[domain_ip][0]
        passwd = domain_dict[domain_ip][1]
    else:
        logger.error('Unable to find {} in global domain_dict : {}' \
                .format(domain_ip, domain_dict))
        return handle

    logger.warning('Trying to set a new {} connection for {}' \
                    .format(conn_type, domain_ip))

    if conn_type == 'cli':
        try:
            handle = ConnectHandler(device_type='cisco_nxos',
                                    host=domain_ip,
                                    username=user,
                                    password=passwd,
                                    timeout=CONNECTION_TIMEOUT)
        except Exception as e:
            logger.error('ConnectHandler failed for domain {}. {} : {}' \
                            .format(domain_ip, type(e).__name__, e))
        else:
            logger.debug('Connection type {} UP for {}' \
                            .format(conn_type, domain_ip))
        finally:
            return handle
    if conn_type == 'sdk':
        try:
            handle = UcsHandle(domain_ip, user, passwd)
        except Exception as e:
            logger.error('UcsHandle failed for domain {}. {} : {}' \
                    .format(domain_ip, type(e).__name__, e))
        else:
            try:
                handle.login(timeout=CONNECTION_TIMEOUT)
            except Exception as e:
                logger.error('UcsHandle {} unable to login to {} in {} ' \
                'seconds : {} : {}'.format(handle, domain_ip, \
                CONNECTION_TIMEOUT, type(e).__name__, e))
                handle = None
            else:
                logger.debug('Connection type {} UP for {}' \
                            .format(conn_type, domain_ip))
        finally:
            return handle

def unpickle_connections():
    """
    Try to unpickle connections to UCS to re-use open connections

    It is expected that open UCS connections are pickled (saved) for re-use.
    Try to use the previously open connections instead of opening a new one
    every time. Read access information of UCS domains from domain_dict and
    populate connection handles in pickled_connections

    Pickling of the UcsHandle works but does not work for
    netmiko.ConnectHandler (TODO). As per original research, opening a new
    SSH session to UCS domain, connect to FI-A, execute a command, connect to
    FI-B, execute a command and finally leave the session at local-mgmt takes
    14 seconds. An already open SSH session can save 4-5 seconds. With
    multithreading and polling_interval of 60seconds, it is ok to open a new
    SSH session everytime but it would be better to open SSH session just once
    and re-use it every time.

    TODO: Explore ways to keep the ssh session open

    Parameters:
    None

    Returns:
    None

    """

    global domain_dict
    global pickled_connections
    existing_pickled_sessions = {}
    pickle_file_name = FILENAME_PREFIX + '_' + INPUT_FILE_PREFIX + '.pickle'
    sdk_time = 0

    try:
        # Do not open with w+b here. This overwrites the file and gives an
        # EOFError
        pickle_file = open(pickle_file_name, 'rb')
    except FileNotFoundError as e:
        logger.info('{} : {} : {}.\nRunning first time?' \
                        .format(pickle_file_name, type(e).__name__, e))
    except Exception as e:
        logger.error('Error in opening {} : {} : {}. Exit.' \
                        .format(pickle_file_name, type(e).__name__, e))
        sys.exit()
    else:
        try:
            existing_pickled_sessions = pickle.load(pickle_file)
        except EOFError as e:
            logger.error('Error in loading {} : {} : {}. Still continue...' \
                            .format(pickle_file_name, type(e).__name__, e))
        except Exception as e:
            logger.error('Error in loading {} : {} : {}. Exiting...' \
                            .format(pickle_file_name, type(e).__name__, e))
            pickle_file.close()
            sys.exit()
        pickle_file.close()

    for domain_ip, item in domain_dict.items():
        pickled_connections[domain_ip] = {}
        cli_handle = None
        sdk_handle = None
        logger.info('Trying to unpickle connection for {}'.format(domain_ip))

        if domain_ip in existing_pickled_sessions:
            logger.info('Found {} in {}'.format(domain_ip, pickle_file_name))
            cli_handle = existing_pickled_sessions[domain_ip]['cli']
            sdk_handle = existing_pickled_sessions[domain_ip]['sdk']
            sdk_time = existing_pickled_sessions[domain_ip]['sdk_time']
            if cli_handle is None or not cli_handle.is_alive():
                '''
                logger.info('Invalid or dead cli_handle for {}. ' \
                'existing_pickled_sessions: {}'.format(domain_ip, \
                existing_pickled_sessions))
                '''
                cli_handle = None
            if sdk_handle is None or not sdk_handle.is_valid():
                logger.warning('Invalid or dead sdk_handle for {}. ' \
                'existing_pickled_sessions: {}'.format(domain_ip, \
                existing_pickled_sessions))
                sdk_handle = None
                sdk_time = 0
        else:
            logger.warning('Not found {} in existing_pickled_sessions: {}: {}' \
            .format(domain_ip, pickle_file_name, existing_pickled_sessions))

        pickled_connections[domain_ip]['cli'] = cli_handle
        pickled_connections[domain_ip]['sdk'] = sdk_handle
        pickled_connections[domain_ip]['sdk_time'] = sdk_time

    logger.debug('Updating global pickled_connections as {}' \
                    .format(pickled_connections))

def cleanup_ucs_connections():
    """
    Clean up UCS connections from the global conn_dict

    Parameters:
    None

    Returns:
    None

    """
    for domain_ip, handles in conn_dict.items():
        cli_handle = handles['cli']
        sdk_handle = handles['sdk']
        logger.debug('Disconnect/Logout session for {} : CLI : {}, SDK : {}'. \
                    format(domain_ip, cli_handle, sdk_handle))
        cli_handle.disconnect()
        sdk_handle.logout()

    # Write an empty dictionary in pickle_file for next time
    pickle_file_name = FILENAME_PREFIX + '_' + INPUT_FILE_PREFIX + '.pickle'
    empty_dict = {}

    try:
        pickle_file = open(pickle_file_name, 'w+b')
    except Exception as e:
        logger.error('Error in opening {} : {} : {}. Exit.' \
                        .format(pickle_file_name, type(e).__name__, e))
    else:
        logger.debug('No pickle sessions for next time in {}' \
                        .format(pickle_file_name))
        pickle.dump(empty_dict, pickle_file)
        pickle_file.close()


def pickle_connections():
    """
    Pickle the global pickled_connections dictionary

    The saved sessions are to be used next time instead of opening a new
    session everytime

    Parameters:
    None

    Returns:
    None

    """

    if user_args['dont_save_sessions']:
        logger.debug('-dss flag. Do not pickle sessions. Clean up now')
        cleanup_ucs_connections()
        return

    global conn_dict
    pickle_file_name = FILENAME_PREFIX + '_' + INPUT_FILE_PREFIX + '.pickle'

    '''
    Following block of code sets all the cli_handle to None because they
    can't be pickled. Leave it as it is until a solution is found
    Do this at the very end to avoid any access issues with conn_dict
    '''
    for domain_ip, handles in conn_dict.items():
        handles['cli'] = None

    try:
        pickle_file = open(pickle_file_name, 'w+b')
    except Exception as e:
        logger.error('Error in opening {} : {} : {}. Exit.' \
                        .format(pickle_file_name, type(e).__name__, e))
    else:
        logger.debug('Pickle sessions for next time in {} : {}' \
                        .format(pickle_file_name, conn_dict))
        pickle.dump(conn_dict, pickle_file)
        pickle_file.close()

def connect_and_pull_stats(handle_list):
    """
    Wrapper to connect to UCS domains and pull stats for handle_list
    Pull stats and store in global dictionaries raw_cli_stats & raw_sdk_stats

    Must be multithreading aware.

    Parameters:
    handle_list (list of IP,handle type,handle). Handle type can be cli or sdk

    Returns:
    None

    """

    global conn_dict
    global pickled_connections
    global raw_sdk_stats
    global raw_cli_stats

    domain_ip = handle_list[0]
    handle_type = handle_list[1]
    fi_id_list = ['A', 'B']

    if handle_type == 'cli':
        cli_handle = handle_list[2]
        if cli_handle is None or not cli_handle.is_alive():
            # logger.info('Invalid or dead cli_handle for {}'.format(domain_ip))
            cli_handle = set_ucs_connection(domain_ip, 'cli')
        conn_dict[domain_ip]['cli'] = cli_handle
        if cli_handle is None:
            logger.error('Exiting for {} due to invalid cli_handle' \
                        .format(domain_ip))
            return

        raw_cli_stats[domain_ip] = {}

        logger.info('CLI pull Starting on {} FI-{}' \
                    .format(domain_ip, fi_id_list))
        for fi_id in fi_id_list:
            logger.info('Connect to NX-OS FI-{} for {}'.format(fi_id, domain_ip))
            cli_handle.send_command('connect nxos ' + fi_id, expect_string='#')
            logger.debug('Connected. Now run commands FI-{} {}' \
                         .format(fi_id,domain_ip))
            raw_cli_stats[domain_ip][fi_id] = {}
            for stats_type, stats_item in cli_stats_types.items():
                raw_cli_stats[domain_ip][fi_id][stats_type] = \
                    cli_handle.send_command(stats_item[0], expect_string='#')
                logger.debug('-- {} -- on {} FI-{}'\
                                .format(stats_item[0], domain_ip, fi_id))
            cli_handle.send_command('exit', expect_string='#')

        logger.info('CLI pull completed on {}'.format(domain_ip))

    if handle_type == 'sdk':
        sdk_handle = handle_list[2]
        conn_time = 0
        if sdk_handle is not None and \
                        'sdk_time' in pickled_connections[domain_ip]:
            conn_time = pickled_connections[domain_ip]['sdk_time']
            logger.debug('SDK connection time:{}. Now:{}. Elapsed:{}'.\
                         format(conn_time, int(time.time()),\
                         ((int(time.time())) - conn_time)))
            if ((int(time.time())) - conn_time > CONNECTION_REFRESH_INTERVAL):
                logger.info('SDK connection refresh time')
                sdk_handle.logout()

        if sdk_handle is None or not sdk_handle.is_valid():
            logger.warning('Invalid or dead sdk_handle for {}'. \
                            format(domain_ip))
            sdk_handle = set_ucs_connection(domain_ip, 'sdk')
            conn_time = int(time.time())
            logger.info('New SDK connection time:{}'.format(conn_time))

        conn_dict[domain_ip]['sdk'] = sdk_handle
        conn_dict[domain_ip]['sdk_time'] = conn_time

        if sdk_handle is None:
            logger.error('Exiting for {} due to invalid sdk_handle' \
                        .format(domain_ip))
            return

        raw_sdk_stats[domain_ip] = {}
        logger.info('Query class_ids for {}'.format(domain_ip))
        raw_sdk_stats[domain_ip] = sdk_handle.query_classids(class_ids)
        logger.info('Query completed {}'.format(domain_ip))


def get_ucs_stats():
    """
    Connect to UCS domains and pull stats

    Use the global pickled_connections. If open connections do not exist or
    dead, open new connections.
    Must be multithreading aware.

    Parameters:
    None

    Returns:
    None

    """

    global pickled_connections
    executor_list = []
    for domain_ip, handles in pickled_connections.items():
        for handle_type, handle in handles.items():
            list_to_add = []
            list_to_add.append(domain_ip)
            list_to_add.append(handle_type)
            list_to_add.append(handle)
            executor_list.append(list_to_add)

    logger.debug('Updated executor_list from pickled_connections : {}' \
                 .format(executor_list))
    logger.debug('Connect and pull stats')

    '''
    Following is a concurrent way of accessing multiple UCS domains,
    using multithreading
    '''
    with \
        concurrent.futures.ThreadPoolExecutor(max_workers=(len(executor_list)))\
        as e:
        for executor in executor_list:
            e.submit(connect_and_pull_stats, executor)

    '''
    Following is a non-concurrent way of accessing multiple UCS domains
    for executor in executor_list:
        connect_and_pull_stats(executor)
    '''

def get_fi_id_from_dn(dn):
    if 'A' in dn:
        return 'A'
    elif 'B' in dn:
        return 'B'
    else:
        return None

def parse_fi_env_stats(domain_ip, top_sys, net_elem, system_stats):
    """
    Use the output of query_classid from UCS to update global stats_dict

    Parameters:
    domain_ip (IP Address of the UCS domain)
    top_sys (managedobjectlist of classid = TopSystem)
    net_elem (managedobjectlist of classid = NetworkElement)
    system_stats (managedobjectlist of classid = SwSystemStats)

    Returns:
    None

    """

    global stats_dict
    d_dict = stats_dict[domain_ip]

    logger.info('Parse env_stats for {}'.format(domain_ip))
    for item in top_sys:
        d_dict['mode'] = item.mode
        d_dict['name'] = item.name
        uptime_list = (item.system_up_time).split(':')
        uptime = ((int)(uptime_list[0]) * 24 * 60 * 60) + \
                    ((int)(uptime_list[1]) * 60 * 60) + \
                        ((int)(uptime_list[2]) * 60) + (int)(uptime_list[3])
        d_dict['uptime'] = uptime

    for item in net_elem:
        fi_id = get_fi_id_from_dn(item.dn)
        if fi_id is None:
            logger.error('Unknown FI ID from {}\n{}'.format(domain_ip, item))
            continue
        fi_dict = d_dict[fi_id]
        fi_dict['total_memory'] = item.total_memory
        fi_dict['oob_if_ip'] = item.oob_if_ip
        fi_dict['serial'] = item.serial
        fi_dict['model'] = item.model

    for item in system_stats:
        fi_id = get_fi_id_from_dn(item.dn)
        if fi_id is None:
            logger.error('Unknow FI ID from {}\n{}'.format(domain_ip, item))
            continue
        fi_dict = d_dict[fi_id]
        fi_dict['load'] = item.load
        fi_dict['mem_available'] = item.mem_available

    logger.info('Done: Parse env_stats for {}'.format(domain_ip))

def get_fi_port_dict(d_dict, dn, transport):
    """
    Either makes a new key into fi_port_dict dictionary or return an existing
    key where stats and other values for that port are stored

    Parameters:
    d_dict (Dictionary where stats for the port are to be stored)
    dn (DN of the port)
    proto (Protocol type, FC or Eth)

    Returns:
    port_dict (Item in stat_dict for the given dn port)

    """

    port_dict = None
    dn_list = dn.split('/')
    fi_id = get_fi_id_from_dn(dn)
    if fi_id is None:
        logger.error('Unknow FI ID from {}'.format(dn))
        return None
    fi_port_dict = d_dict[fi_id]['fi_ports']

    # First handle port-channel case
    if 'pc-' in dn:
        '''
        Handle class FabricDceSwSrvPc for PC between FI and IOM
        dn in FabricDceSwSrvPc fabric/server/sw-B/pc-1154
        dn in FabricFcSanPc fabric/san/B/pc-3
        '''
        pc_id = ((str)(dn_list[3])).upper()
        if 'FC' in transport:
            pc_id = 'SAN-' + pc_id
        if 'Eth' in transport and 'server' not in dn:
            pc_id = 'LAN-' + pc_id
        if pc_id not in fi_port_dict:
            fi_port_dict[pc_id] = {}
        port_dict = fi_port_dict[pc_id]
        return port_dict

    slot_id = ((str)(dn_list[2])).replace('slot-', '')

    if 'FC' in transport:
        port_id = ((str)(dn_list[4])).replace('port-', '')

        # Prefix single digit port number with 0 to help sorting
        if len(port_id) == 1:
            port_id = '0' + port_id

        if (slot_id + '/' + port_id) not in fi_port_dict:
            fi_port_dict[slot_id + '/' + port_id] = {}
            fi_port_dict[slot_id + '/' + port_id]['channel'] = 'No'
        port_dict = fi_port_dict[slot_id + '/' + port_id]

    if 'Eth' in transport:
        '''
         Handle the breakout case of single 40GbE port into 4x10GbE ports
         Without breakout dn example:
            sys/switch-B/slot-1/switch-ether/port-34
         With breakout dn example:
           sys/switch-A/slot-1/switch-ether/aggr-port-25/port-1
        '''
        if 'aggr-port' in dn:
            port_id = ((str)(dn_list[4])).replace('aggr-port-', '')
            if len(port_id) is 1:
                port_id = '0' + port_id
            sub_port_id = ((str)(dn_list[5])).replace('port-', '')
            if len(sub_port_id) is 1:
                sub_port_id = '0' + sub_port_id
            port_id = port_id + '/' + sub_port_id
        else:
            port_id = ((str)(dn_list[4])).replace('port-', '')
            if len(port_id) is 1:
                port_id = '0' + port_id

        if (slot_id + '/' + port_id) not in fi_port_dict:
            fi_port_dict[slot_id + '/' + port_id] = {}
            fi_port_dict[slot_id + '/' + port_id]['channel'] = 'No'
        port_dict = fi_port_dict[slot_id + '/' + port_id]

    return port_dict

def get_speed_num_from_string(speed):
    '''
    oper_speed can be 0 or indeterminate or 10 or 10gbps
    * replace indeterminate by 0
    * strip off gbps, just keep 10
    '''
    if 'gbps' in (str)(speed):
        return (int)(((str)(speed)).rstrip('gbps'))
    elif 'ndeterminat' in (str)(speed):
        return 0
    else:
        return (int)(speed)

def fill_fi_port_common_items(port_dict, item):
    port_dict['if_role'] = item.if_role
    port_dict['oper_state'] = item.oper_state
    port_dict['admin_state'] = item.admin_state
    # name carries description
    port_dict['name'] = item.name
    port_dict['oper_speed'] = get_speed_num_from_string(item.oper_speed)

def parse_fi_stats(domain_ip, fcpio, sanpc, sanpcep, fcstats, fcerr, ethpio,
                   lanpc, lanpcep, ethrx, ethtx, etherr, ethloss, srvpc,
                   srvpcep):
    """
    Use the output of query_classid from UCS to update global stats_dict

    Parameters:
    domain_ip (IP Address of the UCS domain)
    fcpio (managedobjectlist as returned by FcPIo)
    sanpc (managedobjectlist as returned by FabricFcSanPc)
    sanpcep (managedobjectlist as returned by FabricFcSanPcEp)
    fcstats (managedobjectlist as returned by FcStats)
    fcerr (managedobjectlist as returned by FcErrStats)
    ethpio (managedobjectlist as returned by EtherPIo)
    lanpc (managedobjectlist as returned by FabricEthLanPc)
    lanpcep (managedobjectlist as returned by FabricEthLanPcEp)
    ethrx (managedobjectlist as returned by EtherRxStats)
    ethtx (managedobjectlist as returned by EtherTxStats)
    etherr (managedobjectlist as returned by EtherErrStats)
    ethloss (managedobjectlist as returned by EtherLossStats)

    ** FabricDceSwSrvPc and FabricDceSwSrvPcEp are for port-channels between
    FI and IOMs
    srvpc (managedobjectlist as returned by FabricDceSwSrvPc)
    srvpcep (managedobjectlist as returned by FabricDceSwSrvPcEp)


    Returns:
    None

    """

    global stats_dict
    domain_dict = stats_dict[domain_ip]

    logger.info('Parse fi_stats for {}'.format(domain_ip))
    for item in fcpio:
        port_dict = get_fi_port_dict(domain_dict, item.dn, 'FC')
        if port_dict is None:
            logger.error('Invalid port_dict for {}\n{}'.format(domain_ip,
                                                               item))
            continue
        port_dict['transport'] = 'FC'
        fill_fi_port_common_items(port_dict, item)

    for item in sanpc:
        port_dict = get_fi_port_dict(domain_dict, item.dn, 'FC')
        if port_dict is None:
            logger.error('Invalid port_dict for {}\n{}'.format(domain_ip,
                                                               item))
            continue
        port_dict['transport'] = 'FC'
        fill_fi_port_common_items(port_dict, item)

    for item in sanpcep:
        '''
        Populate port-channel information for this port
        Passon ep_dn from FabricFcSanPcEp which contains the dn of the
        physical port.
        For member of a port-channel, set channel=<port_name_of_PC>
        For non-member or a port-channel, set channel=No
        For PC interfaces, do not set channel at all
        '''
        port_dict = get_fi_port_dict(domain_dict, item.ep_dn, 'FC')
        if port_dict is None:
            logger.error('Invalid port_dict for {}\n{}'.format(domain_ip, item))
            continue
        port_dict['channel'] = ((item.dn.split('/'))[1] + '-' \
                                    +(item.dn.split('/'))[3]).upper()

    for item in ethpio:
        # ethrx also contains stats for stats of traces between IOM and blades
        # handle them in parse_backplane_port_stats
        if 'switch-' not in item.dn:
            continue
        port_dict = get_fi_port_dict(domain_dict, item.dn, 'Eth')
        if port_dict is None:
            logger.error('Invalid port_dict for {}\n{}'.format(domain_ip,
                                                               item))
            continue
        port_dict['transport'] = 'Eth'
        fill_fi_port_common_items(port_dict, item)
        if (str)(item.if_role) == 'server':
            port_dict['to_chassis'] = 'chassis-' + (str)(item.chassis_id)
            port_dict['to_iom_slot'] = (str)(item.peer_slot_id)
            port_dict['to_iom_port'] = (str)(item.peer_port_id)

    for item in lanpc:
        port_dict = get_fi_port_dict(domain_dict, item.dn, 'Eth')
        if port_dict is None:
            logger.error('Invalid port_dict for {}\n{}'.format(domain_ip, item))
            continue
        port_dict['transport'] = 'Eth'
        fill_fi_port_common_items(port_dict, item)

    for item in lanpcep:
        '''
        Populate port-channel information for this port
        Passon ep_dn from FabricEthLanPcEp which contains the dn of the
        physical port.
        For member of a port-channel, set channel=<port_name_of_PC>
        For non-member or a port-channel, set channel=No
        For PC interfaces, do not set channel at all
        '''
        port_dict = get_fi_port_dict(domain_dict, item.ep_dn, 'Eth')
        if port_dict is None:
            logger.error('Invalid port_dict for {}\n{}'.format(domain_ip, item))
            continue
        port_dict['channel'] = ((item.dn.split('/'))[1] + '-' \
                                    +(item.dn.split('/'))[3]).upper()

    for item in srvpc:
        port_dict = get_fi_port_dict(domain_dict, item.dn, 'Eth')
        if port_dict is None:
            logger.error('Invalid port_dict for {}\n{}'.format(domain_ip, item))
            continue
        port_dict['transport'] = 'Eth'
        fill_fi_port_common_items(port_dict, item)

    for item in srvpcep:
        '''
        Populate port-channel information for this port
        Passon ep_dn from FabricDceSwSrvPcEp which contains the dn of the
        physical port.
        For member of a port-channel, set channel=<port_name_of_PC>
        For non-member or a port-channel, set channel=No
        For PC interfaces, do not set channel at all
        '''
        port_dict = get_fi_port_dict(domain_dict, item.ep_dn, 'Eth')
        if port_dict is None:
            logger.error('Invalid port_dict for {}\n{}'.format(domain_ip, item))
            continue
        port_dict['channel'] = ((item.dn.split('/'))[3]).upper()

    for item in fcstats:
        port_dict = get_fi_port_dict(domain_dict, item.dn, 'FC')
        if port_dict is None:
            logger.error('Invalid port_dict for {}\n{}'.format(domain_ip, item))
            continue
        port_dict['bytes_rx_delta'] = item.bytes_rx_delta
        port_dict['bytes_tx_delta'] = item.bytes_tx_delta

    for item in fcerr:
        port_dict = get_fi_port_dict(domain_dict, item.dn, 'FC')
        if port_dict is None:
            logger.error('Invalid port_dict for {}\n{}'.format(domain_ip, item))
            continue
        port_dict['discard_rx_delta'] = item.discard_rx_delta
        port_dict['discard_tx_delta'] = item.discard_tx_delta
        port_dict['crc_rx_delta'] = item.crc_rx_delta
        port_dict['sync_losses_delta'] = item.sync_losses_delta
        port_dict['signal_losses_delta'] = item.signal_losses_delta
        port_dict['link_failures_delta'] = item.link_failures_delta

    for item in ethrx:
        # ethrx also contains stats of backplane ports between IOM and blades
        # handle them in parse_backplane_port_stats
        if 'switch-' not in item.dn:
            continue
        port_dict = get_fi_port_dict(domain_dict, item.dn, 'Eth')
        if not port_dict:
            logger.error('Invalid port_dict for {}\n{}'.format(domain_ip, item))
            continue
        port_dict['bytes_rx_delta'] = item.total_bytes_delta

    for item in ethtx:
        # ethtx also contains stats of backplane ports between IOM and blades
        # handle them in parse_backplane_port_stats
        if 'switch-' not in item.dn:
            continue
        port_dict = get_fi_port_dict(domain_dict, item.dn, 'Eth')
        if not port_dict:
            logger.error('Invalid port_dict for {}\n{}'.format(domain_ip, item))
            continue
        port_dict['bytes_tx_delta'] = item.total_bytes_delta

    for item in etherr:
        # Also contains stats of backplane ports between IOM and blades
        # handle them in parse_backplane_port_stats
        if 'switch-' not in item.dn:
            continue
        port_dict = get_fi_port_dict(domain_dict, item.dn, 'Eth')
        if not port_dict:
            logger.error('Invalid port_dict for {}\n{}'.format(domain_ip, item))
            continue
        port_dict['out_discard_delta'] = item.out_discard_delta

    for item in ethloss:
        # Also contains stats of backplane ports between IOM and blades
        # handle them in parse_backplane_port_stats
        if 'switch-' not in item.dn:
            continue
        port_dict = get_fi_port_dict(domain_dict, item.dn, 'Eth')
        if not port_dict:
            logger.error('Invalid port_dict for {}\n{}'.format(domain_ip, item))
            continue
        port_dict['giants_delta'] = item.giants_delta

    logger.info('Done: Parse fi_stats for {}'.format(domain_ip))

def fill_chassis_dict(item, chassis_dict):
    if item.lc != 'allocated':
        logger.warning('Not allocated lc:{} for DN:{}'.format(item.lc, item.dn))
        return

    # dn format: sys/chassis-1/blade-2/adaptor-1/host-fc-4
    # vnic_dn format: org-root/ls-SP-blade-m200-2/fc-vHBA-B2
    # peer_dn format: sys/chassis-1/slot-1/host/port-3
    dn_list = (item.dn).split('/')
    chassis = dn_list[1]
    blade = dn_list[2]
    adaptor = dn_list[3]
    vif_name = item.name

    peer_dn_list = (item.peer_dn).split('/')
    iom_slot = (peer_dn_list[2]).replace('slot-', '')
    iom_port = (peer_dn_list[-1]).replace('port-', '')
    if len(iom_port) == 1:
        iom_port = '0' + iom_port

    # Store the port in x/y format in iom_port
    iom_port = iom_slot + '/' + iom_port

    sp = ((item.vnic_dn).split('/'))[-2].replace('ls-', '')

    '''
    Initiatize dictionary structure in following format
    'chassis':
      'chassis-1':
        'blades':
          'blade-1':
            'adaptors':
              'adaptor-1':
                'vifs':
                  'vHBA-1':
    '''
    if chassis not in chassis_dict:
        chassis_dict[chassis] = {}
    per_chassis_dict = chassis_dict[chassis]
    if 'blades' not in per_chassis_dict:
        per_chassis_dict['blades'] = {}
    blade_dict = per_chassis_dict['blades']
    if blade not in blade_dict:
        blade_dict[blade] = {}
    per_blade_dict = blade_dict[blade]
    if 'adaptors' not in per_blade_dict:
        per_blade_dict['adaptors'] = {}
    adaptor_dict = per_blade_dict['adaptors']
    if adaptor not in adaptor_dict:
        adaptor_dict[adaptor] = {}
    per_adaptor_dict = adaptor_dict[adaptor]
    if 'vifs' not in per_adaptor_dict:
        per_adaptor_dict['vifs'] = {}
    vif_dict = per_adaptor_dict['vifs']
    if vif_name not in vif_dict:
        vif_dict[vif_name] = {}
    per_vif_dict = vif_dict[vif_name]

    # Fill up now
    per_blade_dict['service_profile'] = sp
    per_vif_dict['iom_backplane_port'] = iom_port
    per_vif_dict['admin_state'] = item.admin_state
    per_vif_dict['link_state'] = item.link_state
    per_vif_dict['rn'] = item.rn
    if 'eth' in item.dn:
        per_vif_dict['transport'] = 'Eth'
    elif 'fc' in item.dn:
        per_vif_dict['transport'] = 'FC'

def get_vif_dict(chassis_dict, chassis, blade, adaptor):
    if chassis not in chassis_dict:
        return None
    per_chassis_dict = chassis_dict[chassis]
    if 'blades' not in per_chassis_dict:
        return None
    blade_dict = per_chassis_dict['blades']
    if blade not in blade_dict:
        return None
    per_blade_dict = blade_dict[blade]
    if 'adaptors' not in per_blade_dict:
        return None
    adaptor_dict = per_blade_dict['adaptors']
    if adaptor not in adaptor_dict:
        return None
    per_adaptor_dict = adaptor_dict[adaptor]
    if 'vifs' not in per_adaptor_dict:
        return None
    return per_adaptor_dict['vifs']

def parse_vnic_stats(domain_ip, vnic_stats, host_ethif, host_fcif, dcxvc):
    """
    Use the output of query_classid from UCS to update global stats_dict

    Parameters:
    domain_ip (IP Address of the UCS domain)
    vnic_stats (managedobjectlist of class_id = AdaptorVnicStats)
    host_ethif (managedobjectlist of classid = AdaptorHostEthIf)
    host_fcif (managedobjectlist of classid = AdaptorHostFcIf)
    dcxvc (managedobjectlist of classid = DcxVc)

    Returns:
    None

    """

    global stats_dict
    domain_dict = stats_dict[domain_ip]
    chassis_dict = domain_dict['chassis']

    logger.info('Parse vnic_stats for {}'.format(domain_ip))
    for item in host_fcif:
        # No support for RACK-MOUNT
        if 'rack-unit' in item.dn:
            continue
        fill_chassis_dict(item, chassis_dict)

    for item in host_ethif:
        # No support for RACK-MOUNT
        if 'rack-unit' in item.dn:
            continue
        fill_chassis_dict(item, chassis_dict)

    '''
    DcxVC contains pinned uplink port. If oper_border_port_id == 0, discard
    if oper_border_slot_id, it is a port-channel
    else, a physical port
    dn format: sys/chassis-1/blade-2/fabric-A/path-1/vc-1355
    '''
    for item in dcxvc:
        # No support for RACK-MOUNT
        if 'rack-unit' in item.dn:
            continue
        if item.vnic == '' or (int)(item.oper_border_port_id) == 0:
            continue

        dn_list = (item.dn).split('/')
        chassis = dn_list[1]
        blade = dn_list[2]
        adaptor = (dn_list[4]).replace('path', 'adaptor')
        vif_name = item.vnic
        uplink_fi_id = item.switch_id

        vif_dict = get_vif_dict(chassis_dict, chassis, blade, adaptor)
        if vif_dict is None:
            continue
        per_vif_dict = vif_dict[vif_name]
        if per_vif_dict is None:
            continue
        if (int)(item.oper_border_slot_id) == 0:
            if 'fc' in item.transport:
                pc_prefix = 'SAN-PC-'
            if 'ether' in item.transport:
                pc_prefix = 'LAN-PC-'
            pinned_uplink = pc_prefix + (str)(item.oper_border_port_id)
        else:
            if len(item.oper_border_port_id) == 1:
                port_id = '0' + (str)(item.oper_border_port_id)
            else:
                port_id = (str)(item.oper_border_port_id)
            pinned_uplink = (str)(item.oper_border_slot_id) + '/' + port_id

        per_vif_dict['pinned_fi_id'] = uplink_fi_id
        per_vif_dict['pinned_fi_uplink'] = pinned_uplink
        if 'fc' in item.transport:
            per_vif_dict['bound_vfc'] = 'vfc' + (str)(item.id)
            per_vif_dict['bound_veth'] = 'veth' + (str)(item.fcoe_id)
        if 'ether' in item.transport:
            per_vif_dict['bound_veth'] = 'veth' + (str)(item.id)

    # dn format: sys/chassis-1/blade-2/adaptor-1/host-fc-4/vnic-stats
    for item in vnic_stats:
        # No support for RACK-MOUNT
        if 'rack-unit' in item.dn:
            continue
        dn_list = (item.dn).split('/')
        chassis = dn_list[1]
        blade = dn_list[2]
        adaptor = dn_list[3]
        rn = dn_list[4]
        vif_dict = get_vif_dict(chassis_dict, chassis, blade, adaptor)
        if vif_dict is None:
            continue
        for vif_name, per_vif_dict in vif_dict.items():
            if per_vif_dict['rn'] == rn:
                break
        per_vif_dict['bytes_rx_delta'] = item.bytes_rx_delta
        per_vif_dict['bytes_tx_delta'] = item.bytes_tx_delta
        per_vif_dict['errors_rx_delta'] = item.errors_rx_delta
        per_vif_dict['errors_tx_delta'] = item.errors_tx_delta
        per_vif_dict['dropped_rx_delta'] = item.dropped_rx_delta
        per_vif_dict['dropped_tx_delta'] = item.dropped_tx_delta

    logger.info('Done: Parse vnic_stats for {}'.format(domain_ip))

def get_bp_port_dict(chassis_dict, dn):
    """
    Either makes a new key into bp_port_dict dictionary or return an existing
    key where stats and other values for that port are stored

    Parameters:
    chassis_dict (Dictionary where stats for the port are to be stored)
    dn (DN of the port)

    Returns:
    port_dict (Item in stat_dict for the given dn port)

    """

    port_dict = None
    dn_list = dn.split('/')
    chassis = dn_list[1]
    slot_id = (dn_list[2]).replace('slot-', '')

    # First handle port-channel case
    if 'pc-' in dn:
        '''
        Handle class EtherServerIntFIoPc for PC between IOM and blade
        dn in EtherServerIntFIoPc sys/chassis-1/blade-7/fabric-A/pc-1290
        '''
        port_id = (dn_list[4]).upper()
    else:
        port_id = (dn_list[4]).replace('port-', '')
        # Make single digit into 2 digit numbers to help with sorting
        if len(port_id) == 1:
            port_id = '0' + port_id

    '''
    Initiaze or reutrn dictionary of following format
    'chassis':
      'chassis-1':
        'bp_ports':
          '1':
            '22':
              'channel':'no'
    '''
    if chassis not in chassis_dict:
        chassis_dict[chassis] = {}
    per_chassis_dict = chassis_dict[chassis]
    if 'bp_ports' not in per_chassis_dict:
        per_chassis_dict['bp_ports'] = {}
    bp_port_dict = per_chassis_dict['bp_ports']
    if slot_id not in bp_port_dict:
        bp_port_dict[slot_id] = {}
    bp_slot_dict = bp_port_dict[slot_id]
    if port_id not in bp_slot_dict:
        bp_slot_dict[port_id] = {}
    per_bp_port_dict = bp_slot_dict[port_id]

    return per_bp_port_dict

def parse_backplane_port_stats(domain_ip, srv_fio, srv_fiopc, srv_fiopcep,
                               ethrx, ethtx, etherr, ethloss, pathep):
    """
    Use the output of query_classid from UCS to update global stats_dict

    Parameters:
    domain_ip (IP Address of the UCS domain)
    srv_fio (managedobjectlist of classid = EtherServerIntFIo)
    srv_fiopc (managedobjectlist of classid = EtherServerIntFIoPc)
    srv_fiopcep (managedobjectlist of classid = EtherServerIntFIoPcEp)
    ethrx (managedobjectlist as returned by EtherRxStats)
    ethtx (managedobjectlist as returned by EtherTxStats)
    etherr (managedobjectlist as returned by EtherErrStats)
    ethloss (managedobjectlist as returned by EtherLossStats)
    pathep (managedobjectlist as returned by FabricPathEp)

    Returns:
    None

    """

    global stats_dict
    d_dict = stats_dict[domain_ip]
    chassis_dict = d_dict['chassis']

    logger.info('Parse backplane ports stats for {}'.format(domain_ip))
    # dn format: sys/chassis-1/slot-2/host/port-30
    # ep_dn format: sys/chassis-1/slot-2/host/pc-1285
    # peer_dn format: sys/chassis-1/blade-8/adaptor-2/ext-eth-5
    for item in srv_fio:
        port_dict = get_bp_port_dict(chassis_dict, item.dn)
        if port_dict is None:
            logger.error('Invalid bp_port_dict for {}\n{}' \
                         .format(domain_ip, item))
            continue
        port_dict['fi_id'] = item.switch_id
        port_dict['admin_state'] = item.admin_state
        port_dict['admin_speed'] = get_speed_num_from_string(item.admin_speed)
        port_dict['oper_state'] = item.oper_state
        port_dict['to_blade'] = ((item.peer_dn).split('/'))[2]
        port_dict['to_adaptor'] = ((item.peer_dn).split('/'))[3]
        port_dict['adaptor_port'] = ((item.peer_dn).split('/'))[4]
        port_dict['channel'] = 'No'

    # dn format: sys/chassis-1/slot-1/host/pc-1290
    for item in srv_fiopc:
        port_dict = get_bp_port_dict(chassis_dict, item.dn)
        if port_dict is None:
            logger.error('Invalid bp_port_dict for {}\n{}' \
                         .format(domain_ip, item))
            continue
        port_dict['fi_id'] = item.switch_id
        port_dict['oper_speed'] = get_speed_num_from_string(item.oper_speed)
        port_dict['oper_state'] = item.oper_state

    # dn format: sys/chassis-1/slot-1/host/pc-1290/ep-slot-1-port-27
    for item in srv_fiopcep:
        '''
        Populate port-channel information for this port
        Passon ep_dn from EtherServerIntFIoPcEp which contains the dn of the
        backplane port.
        For member of a port-channel, set channel=<port_name_of_PC>
        For non-member or a port-channel, set channel=No
        For PC interfaces, do not set channel at all
        '''
        port_dict = get_bp_port_dict(chassis_dict, item.ep_dn)
        if port_dict is None:
            logger.error('Invalid bp_port_dict for {}\n{}' \
                         .format(domain_ip, item))
            continue
        port_dict['channel'] = (((item.dn).split('/'))[4]).upper()

    # dn format: sys/chassis-2/slot-1/host/port-29/rx-stats
    for item in ethrx:
        # ethrx also contains stats of FI ports. Handle them with FI ports
        if 'chassis-' not in item.dn:
            continue
        port_dict = get_bp_port_dict(chassis_dict, item.dn)
        if port_dict is None:
            logger.error('Invalid bp_port_dict for {}\n{}' \
                         .format(domain_ip, item))
            continue
        port_dict['bytes_rx_delta'] = item.total_bytes_delta

    # dn format: sys/chassis-2/slot-1/host/port-29/tx-stats
    for item in ethtx:
        # ethrx also contains stats of FI ports. Handle them with FI ports
        if 'chassis-' not in item.dn:
            continue
        port_dict = get_bp_port_dict(chassis_dict, item.dn)
        if port_dict is None:
            logger.error('Invalid bp_port_dict for {}\n{}' \
                         .format(domain_ip, item))
            continue
        port_dict['bytes_tx_delta'] = item.total_bytes_delta

    '''
    Following code looks up FabricPathEp to find mapping between IOM backplane
    port and FI server ports (which is connected to IOM fabric port)
    Go through it twice, first when locale is chassis and again when locale is
    server
    Read the documentation to understand the logic. It takes a while ...
    '''
    path_dict = {}
    for item in pathep:
        # dn format: sys/chassis-1/blade-3/fabric-A/path-1/ep-mux
        # peer_dn format: sys/chassis-1/slot-2/host/port-5
        if item.locale == 'chassis' and 'blade' in item.dn:
            dn_list = (item.dn).split('/')
            peer_dn_list = (item.peer_dn).split('/')
            fi_id = (dn_list[3]).replace('fabric-', '')
            chassis = dn_list[1]
            path = chassis + '/' + dn_list[2] + '/' + dn_list[3] + \
                                '/' + dn_list[4]
            if item.c_type == 'mux' or \
                    item.c_type == 'mux-fabricpc-to-hostport':
                slot_id = peer_dn_list[2]
                port_id = peer_dn_list[-1]
            if item.c_type == 'mux-fabricport-to-hostpc' or \
                    item.c_type == 'mux-fabricpc-to-hostpc':
                slot_id = peer_dn_list[2]
                port_id = (peer_dn_list[-1]).upper()
            path_dict[path] = slot_id + '/host/' + port_id

    for item in pathep:
        # dn format: sys/chassis-1/blade-3/fabric-A/path-1/ep-mux-fabric
        # peer_dn format: sys/switch-A/slot-1/switch-ether/port-3
        if item.locale == 'server' and 'blade' in item.dn:
            dn_list = (item.dn).split('/')
            peer_dn_list = (item.peer_dn).split('/')
            fi_id = (dn_list[3]).replace('fabric-', '')
            chassis = dn_list[1]
            path = chassis + '/' + dn_list[2] + '/' + dn_list[3] + \
                                '/' + dn_list[4]
            if path not in path_dict:
                continue
            # Construct a DN in sys/chassis-2/slot-1/host/port-29/tx-stats
            # format from slot and port in path_dict
            dn_for_port_dict = 'sys/' + chassis + '/' + path_dict[path]
            port_dict = get_bp_port_dict(chassis_dict, dn_for_port_dict)
            if port_dict is None:
                logger.error('Invalid bp_port_dict for {}\n{}' \
                             .format(domain_ip, item))
                continue

            fi_slot = ''
            fi_port = ''
            if item.c_type == 'mux-fabric':
                fi_slot = (peer_dn_list[2]).replace('slot-', '') + '/'
                fi_port = (peer_dn_list[-1]).replace('port-', '')
                if len(fi_port) == 1:
                    fi_port = '0' + fi_port
            if item.c_type == 'mux-fabricpc':
                fi_port = (peer_dn_list[-1]).upper()

            port_dict['fi_server_port'] = fi_slot + fi_port

    logger.info('Done: Parse backplane ports stats for {}'.format(domain_ip))

def parse_compute_inventory(domain_ip, blade):
    """
    Use the output of query_classid from UCS to update global stats_dict

    Parameters:
    domain_ip (IP Address of the UCS domain)
    blade (managedobjectlist as returned by ComputeBlade)

    Returns:
    None

    """

    global stats_dict
    d_dict = stats_dict[domain_ip]
    chassis_dict = d_dict['chassis']

    logger.info('Parse compute blades for {}'.format(domain_ip))

    # dn format: sys/chassis-1/blade-8
    # assigned_to_dn format: org-root/ls-SP-blade-m200-8
    for item in blade:
        dn_list = (item.dn).split('/')
        chassis = dn_list[1]
        blade = dn_list[2]

        service_profile = (((item.assigned_to_dn).split('/'))[-1]).strip('ls-')
        if 'none' in item.association or len(service_profile) is 0:
            service_profile = 'Unknown'
        # Numbers might be handy in front-end representations/color coding
        if 'ok' in item.oper_state:
            oper_state_code = 0
        else:
            oper_state_code = 1
        # By this time, the stats_dict should be fully initialized for all
        # available chassis and blades. Still make sure of unexpected behavior
        if chassis not in chassis_dict:
            chassis_dict[chassis] = {}
        per_chassis_dict = chassis_dict[chassis]
        if 'blades' not in per_chassis_dict:
            per_chassis_dict['blades'] = {}
        blade_dict = per_chassis_dict['blades']
        if blade not in blade_dict:
            blade_dict[blade] = {}
        per_blade_dict = blade_dict[blade]
        per_blade_dict['service_profile'] = service_profile
        per_blade_dict['association'] = item.association
        per_blade_dict['oper_state'] = item.oper_state
        per_blade_dict['oper_state_code'] = oper_state_code
        per_blade_dict['operability'] = item.operability
        per_blade_dict['admin_state'] = item.admin_state
        per_blade_dict['model'] = item.model
        per_blade_dict['num_cores'] = item.num_of_cores
        per_blade_dict['num_cpus'] = item.num_of_cpus
        per_blade_dict['memory'] = item.available_memory
        per_blade_dict['serial'] = item.serial
        per_blade_dict['num_adaptors'] = item.num_of_adaptors
        per_blade_dict['num_vEths'] = item.num_of_eth_host_ifs
        per_blade_dict['num_vFCs'] = item.num_of_fc_host_ifs

    logger.info('Done: Parse compute blades for {}'.format(domain_ip))

def parse_raw_sdk_stats():
    """
    Update stats_dict by parsing raw_sdk_stats

    Parameters:
    None

    Returns:
    None

    """

    global raw_sdk_stats
    global class_ids

    for domain_ip, obj in raw_sdk_stats.items():
        logger.info('Start parsing SDK stats for {}'.format(domain_ip))
        # There is a strange issue where every 2 hours come of the UCS doamins
        # do not return anything, resulting in empty obj dict. Check for the
        # condition to avoid KeyError exception. Log it properly
        if Counter(class_ids) != Counter(obj.keys()):
            logger.error('Missing returned class ID(s) from {}. Skipping...' \
                         'Value:\n{}'.format(domain_ip, obj))
            continue

        parse_fi_env_stats(domain_ip,
                           obj['TopSystem'],
                           obj['NetworkElement'],
                           obj['SwSystemStats'])

        parse_fi_stats(domain_ip,
                       obj['FcPIo'],
                       obj['FabricFcSanPc'],
                       obj['FabricFcSanPcEp'],
                       obj['FcStats'],
                       obj['FcErrStats'],
                       obj['EtherPIo'],
                       obj['FabricEthLanPc'],
                       obj['FabricEthLanPcEp'],
                       obj['EtherRxStats'],
                       obj['EtherTxStats'],
                       obj['EtherErrStats'],
                       obj['EtherLossStats'],
                       obj['FabricDceSwSrvPc'],
                       obj['FabricDceSwSrvPcEp'])

        parse_vnic_stats(domain_ip,
                         obj['AdaptorVnicStats'],
                         obj['AdaptorHostEthIf'],
                         obj['AdaptorHostFcIf'],
                         obj['DcxVc'])

        parse_backplane_port_stats(domain_ip,
                                   obj['EtherServerIntFIo'],
                                   obj['EtherServerIntFIoPc'],
                                   obj['EtherServerIntFIoPcEp'],
                                   obj['EtherRxStats'],
                                   obj['EtherTxStats'],
                                   obj['EtherErrStats'],
                                   obj['EtherLossStats'],
                                   obj['FabricPathEp'])

        parse_compute_inventory(domain_ip,
                                obj['ComputeBlade'])

def parse_pfc_stats(pfc_output, domain_ip, fi_id):
    """
    Parse PFC stats

    Here is a sample output of the command
    ============================================================
    Port               Mode Oper(VL bmap)  RxPPP      TxPPP
    ============================================================

    Ethernet1/1        Auto Off           0          0
    Ethernet1/2        Auto Off           2          0
    Ethernet1/3        Auto Off           2          0
    Vethernet9547      Auto Off           0          0
    Ethernet1/1/1      Auto On  (8)       0          0
    Ethernet1/1/2      Auto Off           0          0

    backplane port between IOM and adaptor on chassis
    FI reports these ports in x/y/z format, where
    x is chassis_id, y is always 1 and z is the backplane port
    on IOM. In other words, this output does not tell the slot ID
    of the IOM in the chassis. The stats_dict maintain stats per
    chassis and IOM slot ID. By this time, after parsing the SDK
    stats, it is expected that the per_bp_port_dict already has
    fi_id. Use that to fill in at right place. Example
    "bp_ports": {
      "1": {        <== Slot ID in the chassis
        "25": {
          "fi_id": "B",
          ...
          },
        "9": {
          "fi_id": "B",
          ...
          },
      },
      "2": {        <== Slot ID in the chassis
        "9": {
        "fi_id": "A",
        ...
        },
        ...
      }

    Parameters:
    pfc_output (Output of NX-OS command for PFC stats)
    domain_ip (IP address of UCS domain on which command was executed)
    fi_id (A or B)

    Returns:
    None

    """

    global stats_dict
    domain_dict = stats_dict[domain_ip]
    fi_port_dict = domain_dict[fi_id]['fi_ports']
    chassis_dict = domain_dict['chassis']
    iom_slot_id = 0 # Invalid. Update it later
    chassis_id = 0 # Invalid. Update it later

    logger.info('Parse pause stats for {}'.format(domain_ip))
    pfc_op = pfc_output.splitlines()
    for lines in pfc_op:
        line = lines.split()
        if len(line) < 5:
            continue
        # skip the Vethernet
        if line[0].startswith('Eth'):
            # line[0] is port name, -2 is RX, -1 is TX PFC stats
            port_list = line[0].split('/')
            if len(port_list) == 2:
                # port on FI
                slot_id = (port_list[0]).replace('Ethernet', '')
                port_id = port_list[1]
                # Prefix single digit port number with 0 to help sorting
                if len(port_id) == 1:
                    port_id = '0' + port_id
                key = slot_id + '/' + port_id
                if key not in fi_port_dict:
                    fi_port_dict[key] = {}
                fi_port_dict[key]['pause_rx'] = line[-2]
                fi_port_dict[key]['pause_tx'] = line[-1]
            elif len(port_list) == 3:
                chassis_id = (port_list[0]).replace('Ethernet', '')
                chassis_id = 'chassis-' + chassis_id
                # Do not continue with chassis dict not initialized
                # Something else might be wrong
                if chassis_id not in chassis_dict:
                    logger.error('Unable to find {} in {}. Unable to ' \
                                 'update backplane pause stats'.
                                 format(chassis_id, chassis_dict.keys()))
                    return
                bp_port_dict = chassis_dict[chassis_id]['bp_ports']
                for iom_slot, port_dict in bp_port_dict.items():
                    for iom_port, per_bp_port_dict in port_dict.items():
                        # Do not run this loop for than once. All ports of
                        # a IOM are expected to carry same fi_id
                        if per_bp_port_dict['fi_id'] == fi_id:
                            iom_slot_id = iom_slot
                        else:
                            pass
                        break
                port_id = port_list[-1]
                if len(port_id) == 1:
                    port_id = '0' + port_id
                iom_slot_dict = bp_port_dict[iom_slot_id]
                if port_id not in iom_slot_dict:
                    continue
                per_bp_port_dict = iom_slot_dict[port_id]
                per_bp_port_dict['pause_rx'] = line[-2]
                per_bp_port_dict['pause_tx'] = line[-1]

    logger.info('Done: Parse pause stats for {}'.format(domain_ip))

def parse_raw_cli_stats():
    """
    Update stats_dict by parsing raw_cli_stats

    Parameters:
    None

    Returns:
    None

    """

    global raw_cli_stats

    for domain_ip, fi_dict in raw_cli_stats.items():
        logger.info('parse_raw_cli_stats for {}'.format(domain_ip))
        for fi_id, stats_type_dict in fi_dict.items():
            logger.info('FI - {}'.format(fi_id))
            for t, o in stats_type_dict.items():
                logger.info('Stats type - {}'.format(t))
                cli_stats_types[t][1](o, domain_ip, fi_id)

def update_stats_dict():
    """
    Update stats_dict

    Parameters:
    None

    Returns:
    None

    """
    global raw_sdk_stats

    parse_raw_sdk_stats()
    parse_raw_cli_stats()

# Key is the name of the stat, value is a list with first member as the NX-OS
# command and 2nd member as function to process the output (as dispatcher)
cli_stats_types = {'pfc_stats':['show int pri', parse_pfc_stats]
                  }

def main(argv):
    # Initial tasks

    if not pre_checks_passed(argv):
        return
    parse_cmdline_arguments()
    setup_logging()
    logger.info('---------- START ----------')
    get_ucs_domains()
    unpickle_connections()

    # Connect to UCS and pull stats. This section must be multi-threading aware
    try:
        get_ucs_stats()
    except Exception as e:
        logger.error('Exception with get_ucs_stats')

    # Parse the stats returned by UCS
    try:
        update_stats_dict()
    except Exception as e:
        logger.error('Exception with update_stats_dict\n{}'.format(raw_sdk_stats))

    # Print the stats as per the desired output format
    try:
        print_output()
    except Exception as e:
        logger.exception('Exception with print_output:\n' + (str)(e))


    # Final tasks
    pickle_connections()
    logger.info('---------- END ----------')

if __name__ == '__main__':
    main(sys.argv)
