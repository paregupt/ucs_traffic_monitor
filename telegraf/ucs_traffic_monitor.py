#! /usr/bin/python3

__author__ = "Paresh Gupta"
__version__ = "0.46"

import sys
import os
import argparse
import logging
from logging.handlers import RotatingFileHandler
import pickle
import json
import time
import random
import re
from collections import Counter
import concurrent.futures
from ucsmsdk.ucshandle import UcsHandle
from netmiko import ConnectHandler

HOURS_IN_DAY = 24
MINUTES_IN_HOUR = 60
SECONDS_IN_MINUTE = 60
# Default UCS session timeout is 7200s (120m). Logout and login proactively
# every 5400s (90m)
CONNECTION_REFRESH_INTERVAL = 5400
CONNECTION_TIMEOUT = 10
MASTER_TIMEOUT = 48

user_args = {}
FILENAME_PREFIX = __file__.replace('.py', '')
INPUT_FILE_PREFIX = ''

LOGFILE_LOCATION = '/var/log/telegraf/'
LOGFILE_SIZE = 20000000
LOGFILE_NUMBER = 10
logger = logging.getLogger('UTM')

# Dictionary with key as IP and value as list of user and passwd
domain_dict = {}
# Dictionary with key as IP and value as a dictionary of type and handle.
# handle is netmiko.ConnectHandler when type is 'cli'
# handle is UcsHandle when type is 'sdk'
conn_dict = {}

# Tracks response time by CLI and SDK connections and prints before end
# response_time_dict : {
#                       'domain_ip' : {
#                                   'cli_start':'time',
#                                   'cli_login':'time',
#                                   'cli_end':'time',
#                                   'sdk_start':'time',
#                                   'sdk_login':'time',
#                                   'sdk_end':'time'
#                                   }
#                       }
response_time_dict = {}

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
             'FirmwareRunning',
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
             'ComputeBlade',
             'ComputeRackUnit'
            ]

###############################################################################
# BEGIN: Generic functions
###############################################################################

def pre_checks_passed(argv):
    if sys.version_info[0] < 3:
        print('Unsupported with Python 2. Must use Python 3')
        logger.error('Unsupported with Python 2. Must use Python 3')
        return False
    if len(argv) <= 1:
        print('Try -h option for usage help')
        return False

    return True

def parse_cmdline_arguments():
    desc_str = \
    'Pull stats from Cisco UCS domain and print output in different formats \n' + \
    'like InfluxDB Line protocol'
    epilog_str = \
    'This file pulls stats from Cisco UCS and convert it into a database\n' + \
    'insert format. The database can be used by a front-end like Grafana.\n' + \
    'The initial version was coded to insert into InfluxDB. Before \n' + \
    'converting into any specific format (like InfluxDB Line Protocol), \n' + \
    'the data is correlated in a hierarchical dictionary. This dictionary \n' +\
    'can be parsed to output the data into other formats. Overall, \n' + \
    'the output can be extended for other databases also.\n\n' + \
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
    parser.add_argument('-ct', '--connection-timeout', type=int,
                    dest='conn_timeout', default=45, help='Total timeout \
                    in seconds for login/auth and metrics pull (Default:45s)')
    parser.add_argument('-ns', '--no-ssh', dest='no_ssh', \
                    action='store_true', default=False, help='Disable SSH \
                    connection. Will loose PAUSE and other data')
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
    parser.add_argument('-vvvv', '--raw_dump', dest='raw_dump', \
                    action='store_true', default=False, help='Dump raw data')
    args = parser.parse_args()
    user_args['input_file'] = args.input_file
    user_args['verify_only'] = args.verify_only
    user_args['conn_timeout'] = args.conn_timeout
    user_args['no_ssh'] = args.no_ssh
    user_args['dont_save_sessions'] = args.dont_save_sessions
    user_args['output_format'] = args.output_format
    user_args['verbose'] = args.verbose
    user_args['more_verbose'] = args.more_verbose
    user_args['most_verbose'] = args.most_verbose
    user_args['raw_dump'] = args.raw_dump

    global INPUT_FILE_PREFIX
    INPUT_FILE_PREFIX = ((((user_args['input_file']).split('/'))[-1]).split('.'))[0]

def setup_logging():
    this_filename = (FILENAME_PREFIX.split('/'))[-1]
    logfile_location = LOGFILE_LOCATION + this_filename
    logfile_prefix = logfile_location + '/' + this_filename
    try:
        os.mkdir(logfile_location)
    except FileExistsError:
        pass
    except Exception:
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
        if user_args.get('most_verbose') or user_args.get('raw_dump'):
            logger.setLevel(logging.DEBUG)

###############################################################################
# END: Generic functions
###############################################################################

###############################################################################
# BEGIN: Connection and Collector functions
###############################################################################

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
                stats_dict[domain[0]]['ru'] = {}
                stats_dict[domain[0]]['fex'] = {}

                conn_dict[domain[0]] = {}

                response_time_dict[domain[0]] = {}
                response_time_dict[domain[0]]['cli_start'] = 0
                response_time_dict[domain[0]]['cli_login'] = 0
                response_time_dict[domain[0]]['cli_end'] = 0
                response_time_dict[domain[0]]['sdk_start'] = 0
                response_time_dict[domain[0]]['sdk_login'] = 0
                response_time_dict[domain[0]]['sdk_end'] = 0

    if not domain_dict:
        logger.warning('No UCS domains to monitor. Check input file. Exiting.')
        sys.exit()

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
    multithreading and polling_interval of 60 seconds, it is ok to open a new
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
        pickle_file = open(pickle_file_name, 'r+b')
    except FileNotFoundError as e:
        logger.warning('{} : {} : {}. Running first time?' \
                        .format(pickle_file_name, type(e).__name__, e))
    except Exception as e:
        logger.exception('Error in opening {} : {} : {}. Exit.' \
                        .format(pickle_file_name, type(e).__name__, e))
        sys.exit()
    else:
        try:
            existing_pickled_sessions = pickle.load(pickle_file)
        except EOFError as e:
            logger.exception('Error in loading {} : {} : {}. Still continue...' \
                            .format(pickle_file_name, type(e).__name__, e))
        except Exception as e:
            logger.exception('Error in loading {} : {} : {}. Exiting...' \
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
    global response_time_dict
    handle = None
    if domain_ip in domain_dict:
        user = domain_dict[domain_ip][0]
        passwd = domain_dict[domain_ip][1]
    else:
        logger.error('Unable to find {} in global domain_dict : {}' \
                .format(domain_ip, domain_dict))
        return handle

    logger.info('Trying to set a new {} connection for {}' \
                .format(conn_type, domain_ip))

    time_d = response_time_dict[domain_ip]
    if conn_type == 'cli':
        try:
            handle = ConnectHandler(device_type='cisco_nxos',
                                    host=domain_ip,
                                    username=user,
                                    password=passwd,
                                    timeout=user_args.get('conn_timeout'))
        except Exception as e:
            logger.exception('ConnectHandler failed for domain {}. {} : {}' \
                            .format(domain_ip, type(e).__name__, e))
        else:
            time_d['cli_login'] = time.time()
            logger.info('Connection type {} UP for {} in {}s' \
                        .format(conn_type, domain_ip, round(( \
                                time_d['cli_login'] - time_d['cli_start']), 2)))
    if conn_type == 'sdk':
        try:
            handle = UcsHandle(domain_ip, user, passwd)
        except Exception as e:
            logger.exception('UcsHandle failed for domain {}. {} : {}' \
                    .format(domain_ip, type(e).__name__, e))
        else:
            try:
                handle.login(timeout=CONNECTION_TIMEOUT)
            except Exception as e:
                logger.exception('UcsHandle {} unable to login to {} in {} ' \
                'seconds : {} : {}'.format(handle, domain_ip, \
                CONNECTION_TIMEOUT, type(e).__name__, e))
                handle = None
            else:
                logger.info('Connection type {} UP for {}' \
                            .format(conn_type, domain_ip))

    return handle

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
    global response_time_dict

    domain_ip = handle_list[0]
    handle_type = handle_list[1]
    fi_id_list = ['A', 'B']
    time_d = response_time_dict[domain_ip]

    if handle_type == 'cli':
        if user_args.get('no_ssh'):
            logger.warning('Skipping CLI metrics due to --no-ssh flag for {}'. \
                           format(domain_ip))
            return
        time_d['cli_start'] = time.time()
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
            logger.info('Connected. Now run commands FI-{} {}' \
                         .format(fi_id, domain_ip))
            raw_cli_stats[domain_ip][fi_id] = {}
            for stats_type, stats_item in cli_stats_types.items():
                raw_cli_stats[domain_ip][fi_id][stats_type] = \
                    cli_handle.send_command(stats_item[0], expect_string='#')
                logger.info('-- {} -- on {} FI-{}'\
                                .format(stats_item[0], domain_ip, fi_id))
            cli_handle.send_command('exit', expect_string='#')

        time_d['cli_end'] = time.time()
        logger.info('CLI pull completed on {} in {}s'. \
                    format(domain_ip, round((time_d['cli_end'] - \
                                             time_d['cli_login']), 2)))

    if handle_type == 'sdk':
        sdk_handle = handle_list[2]
        time_d['sdk_start'] = time.time()
        conn_time = 0
        if sdk_handle is not None and \
                        'sdk_time' in pickled_connections[domain_ip]:
            conn_time = pickled_connections[domain_ip]['sdk_time']
            # Do no refresh all the connections at the same time
            conn_refresh_time = CONNECTION_REFRESH_INTERVAL + \
                                    random.randint(1, 1500)
            logger.info('SDK connection for {}. Time:{}, Elapsed:{},' \
                        ' Refresh:{}'.format(domain_ip, conn_time, \
                         ((int(time.time())) - conn_time), conn_refresh_time))
            if (int(time.time())) - conn_time > conn_refresh_time:
                logger.info('SDK connection refresh time for {}'. \
                            format(domain_ip))
                sdk_handle.logout()

        if sdk_handle is None or not sdk_handle.is_valid():
            logger.warning('Invalid or dead sdk_handle for {}'. \
                            format(domain_ip))
            sdk_handle = set_ucs_connection(domain_ip, 'sdk')
            if sdk_handle is None:
                conn_dict[domain_ip]['sdk'] = sdk_handle
                conn_dict[domain_ip]['sdk_time'] = 0
                logger.error('Exiting for {} due to invalid sdk_handle' \
                        .format(domain_ip))
                return
            conn_time = int(time.time())
            logger.info('New SDK connection time:{}'.format(conn_time))

        conn_dict[domain_ip]['sdk'] = sdk_handle
        conn_dict[domain_ip]['sdk_time'] = conn_time
        time_d['sdk_login'] = time.time()

        raw_sdk_stats[domain_ip] = {}
        logger.info('Query class_ids for {}'.format(domain_ip))
        raw_sdk_stats[domain_ip] = sdk_handle.query_classids(class_ids)
        time_d['sdk_end'] = time.time()
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
            if handle_type == 'cli' or handle_type == 'sdk':
                list_to_add = []
                list_to_add.append(domain_ip)
                list_to_add.append(handle_type)
                list_to_add.append(handle)
                executor_list.append(list_to_add)

    logger.info('Connect and pull stats: executor_list : {}' \
                 .format(executor_list))
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
        logger.exception('Error in opening {} : {} : {}. Exit.' \
                        .format(pickle_file_name, type(e).__name__, e))
    else:
        logger.info('No pickle sessions for next time in {}' \
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
        logger.exception('Error in opening {} : {} : {}. Exit.' \
                        .format(pickle_file_name, type(e).__name__, e))
    else:
        logger.info('Pickle sessions for next time in {} : {}' \
                        .format(pickle_file_name, conn_dict))
        pickle.dump(conn_dict, pickle_file)
        pickle_file.close()

###############################################################################
# END: Connection and Collector functions
###############################################################################

###############################################################################
# BEGIN: Parser functions
###############################################################################

def get_fi_id_from_dn(dn):
    if 'A' in dn:
        return 'A'
    elif 'B' in dn:
        return 'B'
    else:
        return None

def isFloat(val):
    try:
        float(val)
        return True
    except ValueError:
        return False

def get_speed_num_from_string(speed, item):
    '''
    oper_speed can be 0 or indeterminate or 10 or 10gbps
    * replace indeterminate by 0
    * strip off gbps, just keep 10
    '''
    if isFloat(speed):
        return (int)(speed)
    else:
        if 'gbps' in (str)(speed):
            return (int)(((str)(speed)).rstrip('gbps'))
        elif 'ndeterminat' in (str)(speed):
            return 0
        elif 'auto' in (str)(speed):
            # Sometimes (especially 4th gen FI) oper_speed is auto
            # when port oper_state is sfp-not-present. More strict checks can be
            # added if checking just for auto is not enough
            return 0
        else:
            logger.warning('Unable to parse speed:auto:{}:\n{}-----Ignoring ' \
                            'for now and continuing with 0-----\n----------'\
                            'REPORT THIS ISSUE----------'.format(speed, item),
                           stack_info=True)
            return 0

def fill_fi_port_common_items(port_dict, item):
    port_dict['if_role'] = item.if_role
    port_dict['oper_state'] = item.oper_state
    port_dict['admin_state'] = item.admin_state
    # name carries description
    port_dict['name'] = item.name
    port_dict['oper_speed'] = get_speed_num_from_string(item.oper_speed, item)

def get_vif_dict_from_dn(domain_ip, dn):
    global stats_dict
    d_dict = stats_dict[domain_ip]
    chassis_dict = d_dict['chassis']
    ru_dict = d_dict['ru']

    # dn:sys/chassis-1/blade-2/fabric-A/path-1/vc-1355
    # dn:sys/rack-unit-5/fabric-B/path-1/vc-1324
    # dn:sys/chassis-1/blade-1/adaptor-1/host-eth-2/vnic-stats
    dn_list = (dn).split('/')
    if 'rack-unit' in dn:
        ru = (str)(dn_list[1])
        if 'adaptor' in dn:
            adaptor = dn_list[2]
        else:
            adaptor = ((str)(dn_list[3])).replace('path', 'adaptor')

        if ru not in ru_dict:
            return None
        per_ru_dict = ru_dict[ru]
        if 'adaptors' not in per_ru_dict:
            return None
        adaptor_dict = per_ru_dict['adaptors']
    else:
        chassis = (str)(dn_list[1])
        blade = (str)(dn_list[2])
        if 'adaptor' in dn:
            adaptor = dn_list[3]
        else:
            adaptor = ((str)(dn_list[4])).replace('path', 'adaptor')

        if chassis not in chassis_dict:
            logger.debug('chassis not in chassis_dict')
            return None
        per_chassis_dict = chassis_dict[chassis]
        if 'blades' not in per_chassis_dict:
            logger.debug('blades not in per_chassis_dict')
            return None
        blade_dict = per_chassis_dict['blades']
        if blade not in blade_dict:
            logger.debug('blade not in per_chassis_dict')
            return None
        per_blade_dict = blade_dict[blade]
        if 'adaptors' not in per_blade_dict:
            logger.debug('adaptors not in per_chassis_dict')
            return None
        adaptor_dict = per_blade_dict['adaptors']

    if adaptor not in adaptor_dict:
        logger.debug('adaptor not in per_chassis_dict:{}'.format(adaptor))
        return None
    per_adaptor_dict = adaptor_dict[adaptor]
    if 'vifs' not in per_adaptor_dict:
        logger.debug('vifs not in per_chassis_dict')
        return None
    return per_adaptor_dict['vifs']

def fill_ru_dict(item, ru_dict):
    if item.lc != 'allocated':
        logger.warning('Not allocated lc:{} for DN:{}'.format(item.lc, item.dn))
        return

    # dn:sys/rack-unit-5/adaptor-1/host-eth-8
    dn_list = (item.dn).split('/')
    ru = (str)(dn_list[1])
    adaptor = (str)(dn_list[2])
    vif_name = (str)(item.name)
    fi_id = (str)(item.switch_id)

    '''
    Initiatize dictionary structure in following format
    'ru':
      'rack-unit-1':
        'adaptors':
          'adaptor-1':
            'vifs':
              'vHBA-1':
    '''
    if ru not in ru_dict:
        ru_dict[ru] = {}
    per_ru_dict = ru_dict[ru]
    if 'adaptors' not in per_ru_dict:
        per_ru_dict['adaptors'] = {}
    adaptor_dict = per_ru_dict['adaptors']
    if adaptor not in adaptor_dict:
        adaptor_dict[adaptor] = {}
    per_adaptor_dict = adaptor_dict[adaptor]
    if 'vifs' not in per_adaptor_dict:
        per_adaptor_dict['vifs'] = {}
    vif_dict = per_adaptor_dict['vifs']
    if vif_name not in vif_dict:
        vif_dict[vif_name] = {}
    per_vif_dict = vif_dict[vif_name]

    per_vif_dict['fi_id'] = fi_id
    per_vif_dict['admin_state'] = item.admin_state
    per_vif_dict['link_state'] = item.link_state
    per_vif_dict['rn'] = item.rn
    if 'eth' in item.dn:
        per_vif_dict['transport'] = 'Eth'
    elif 'fc' in item.dn:
        per_vif_dict['transport'] = 'FC'

    # peer_dn:sys/switch-B/slot-1/switch-ether/port-5
    # peer_dn:sys/fex-2/slot-1/host/port-29
    peer_dn_list = (item.peer_dn).split('/')
    peer, peer_type, peer_port = 'unknown', 'unknown', '0/0'
    if len(peer_dn_list) > 3:
        peer_slot = (peer_dn_list[2]).replace('slot-', '')
        peer_port = (peer_dn_list[-1]).replace('port-', '')
        if len(peer_port) == 1:
            peer_port = '0' + peer_port
        # Store the port in x/y format in iom_port
        peer_port = peer_slot + '/' + peer_port
        if 'fex' in item.peer_dn:
            peer_type = 'FEX'
            peer = peer_dn_list[1]
        elif 'switch-' in item.peer_dn:
            peer_type = 'FI'
            peer = ((str)(peer_dn_list[1])).replace('switch', 'FI')
    else:
        logger.debug('Unable to decode peer_dn:{}, dn:{}'. \
                    format(item.peer_dn, item.dn))
    per_vif_dict['peer'] = peer
    per_vif_dict['peer_type'] = peer_type
    per_vif_dict['peer_port'] = peer_port

def fill_chassis_dict(item, domain_ip):
    if item.lc != 'allocated':
        logger.warning('Not allocated lc:{} for DN:{}'.format(item.lc, item.dn))
        return

    d_dict = stats_dict[domain_ip]
    chassis_dict = d_dict['chassis']

    # dn format: sys/chassis-1/blade-2/adaptor-1/host-fc-4
    # vnic_dn format: org-root/ls-SP-blade-m200-2/fc-vHBA-B2
    dn_list = (item.dn).split('/')
    if len(dn_list) < 4:
        logger.warning('Unable to fill_chassis_dict for dn:{}'.format(item.dn))
        return
    chassis = (str)(dn_list[1])
    blade = (str)(dn_list[2])
    adaptor = (str)(dn_list[3])
    vif_name = (str)(item.name)
    fi_id = (str)(item.switch_id)
    fi_dict = d_dict[fi_id]

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

    peer, peer_type, peer_port = 'unknown', 'unknown', '0/0'
    if 'model' in per_blade_dict:
        # peer_dn:sys/chassis-17/slot-2/shared-io-module/fabric/port-5
        if 'UCS-S' in per_blade_dict['model'] or \
            'UCSC-C3K-M4SRB' in per_blade_dict['model']:
            logger.debug('Found S-series {} FI-{} for dn:{}'. \
                            format(per_blade_dict['model'], fi_id, item.dn))
            slot = blade.replace('blade-', '')
            fi_port_dict = fi_dict['fi_ports']
            for fi_port, per_fi_port_dict in fi_port_dict.items():
                if 'peer_type' in per_fi_port_dict:
                    if per_fi_port_dict['peer_type'] != 'S-chassis':
                        continue
                if 'peer' in per_fi_port_dict and \
                    'peer_port' in per_fi_port_dict:
                    fi_port_peer_slot = \
                        ((per_fi_port_dict['peer_port']).split('/'))[0]
                    if per_fi_port_dict['peer'] == chassis and \
                        fi_port_peer_slot == slot:
                        logger.debug('Found peer chassis {} and slot {}'. \
                            format(chassis, fi_port_peer_slot))
                        peer_type = 'FI'
                        peer_port = fi_port
                        peer = 'FI-' + fi_id
                        break
            logger.debug('FI Server ports: peer port:{}, peer_type:{}'. \
                            format(peer_port, peer_type))
            # If not found connected to FI, try FEX
            if peer_type == 'unknown':
                fex_dict = d_dict['fex']
                slot = blade.replace('blade-', '')
                for fex_id, per_fex_dict in fex_dict.items():
                    if 'bp_ports' not in per_fex_dict:
                        continue
                    bp_port_dict = per_fex_dict['bp_ports']
                    for bp_slot_id, bp_slot_dict in bp_port_dict.items():
                        for bp_port_id, per_bp_port_dict in \
                                                    bp_slot_dict.items():
                            if 'fi_id' in per_bp_port_dict:
                                if per_bp_port_dict['fi_id'] != fi_id:
                                    continue
                            if 'peer_type' in per_bp_port_dict:
                                if per_bp_port_dict['peer_type'] != 'S-chassis':
                                    continue
                            if 'peer' in per_bp_port_dict and \
                                'peer_port' in per_bp_port_dict:
                                bp_port_peer_slot = \
                                    ((per_bp_port_dict['peer_port']).split('/'))[0]
                                if per_bp_port_dict['peer'] == chassis and \
                                    bp_port_peer_slot == slot:
                                    logger.debug('Found peer chassis {} and slot {}'. \
                                        format(chassis, bp_port_peer_slot))
                                    peer_type = 'FEX'
                                    peer_port = bp_slot_id + '/' + bp_port_id
                                    peer = fex_id
                                    break
            logger.debug('FEX BP ports: peer port:{}, peer_type:{}'. \
                            format(peer_port, peer_type))
        else:
            # peer_dn format: sys/chassis-1/slot-1/host/port-3
            peer_dn_list = (item.peer_dn).split('/')
            if len(peer_dn_list) > 3:
                peer_slot = re.sub('.*slot-', '', (str)(peer_dn_list[2]))
                peer_port = (peer_dn_list[-1]).replace('port-', '')
                if len(peer_port) == 1:
                    peer_port = '0' + peer_port
            else:
                logger.info('Unable to decode peer_dn:{}, dn:{}'. \
                            format(item.peer_dn, item.dn))
            # Store the port in x/y format in iom_port
            peer_port = peer_slot + '/' + peer_port
            peer_type = 'IOM'
            peer = 'IOM-' + peer_slot
    else:
        logger.debug('Unable to find blade model for dn:{}'.format(item.dn))

    # Fill up now
    per_vif_dict['peer_port'] = peer_port
    per_vif_dict['peer'] = peer
    per_vif_dict['peer_type'] = peer_type
    per_vif_dict['fi_id'] = fi_id
    per_vif_dict['admin_state'] = item.admin_state
    per_vif_dict['link_state'] = item.link_state
    per_vif_dict['rn'] = item.rn
    if 'eth' in item.dn:
        per_vif_dict['transport'] = 'Eth'
    elif 'fc' in item.dn:
        per_vif_dict['transport'] = 'FC'

def get_bp_port_dict_from_dn(domain_ip, dn, create_new):
    """
    Either makes a new key into bp_port_dict dictionary or return an existing
    key where stats and other values for that port are stored

    Parameters:
    domain_ip (IP Address of the UCS domain)
    dn (DN of the port)
    create_new (Create new only if set to True)

    Returns:
    port_dict (Item in stat_dict for the given dn port)

    """

    global stats_dict
    d_dict = stats_dict[domain_ip]

    dn_list = dn.split('/')
    # First handle port-channel case
    if 'pc-' in dn:
        '''
        Handle class EtherServerIntFIoPc for PC between IOM and server
        dn in EtherServerIntFIoPc sys/chassis-1/blade-7/fabric-A/pc-1290
        '''

        port_id = (dn_list[4]).upper()
    else:
        port_id = (dn_list[4]).replace('port-', '')
        # Make single digit into 2 digit numbers to help with sorting
        if len(port_id) == 1:
            port_id = '0' + port_id

    '''
    Initiaze or return dictionary of following format
    'chassis':
      'chassis-1':
        'bp_ports':
          '1':
            '22':
              'channel':'no'
    'fex':
      'fex-1':
        'bp_ports':
          '1':
            '22':
              'channel':'no'
    '''

    # dn:sys/chassis-1/slot-1/host/port-14
    # dn:sys/fex-2/slot-1/host/port-1
    # dn:sys/chassis-1/sw-slot-1/host/port-6 (UCS Mini)
    if 'chassis' in dn:
        chassis_dict = d_dict['chassis']
        chassis = (str)(dn_list[1])
        if chassis not in chassis_dict:
            chassis_dict[chassis] = {}
        per_chassis_dict = chassis_dict[chassis]
        if 'bp_ports' not in per_chassis_dict:
            if not create_new:
                return None
            per_chassis_dict['bp_ports'] = {}
        bp_port_dict = per_chassis_dict['bp_ports']
    elif 'fex' in dn:
        fex_dict = d_dict['fex']
        fex = (str)(dn_list[1])
        if fex not in fex_dict:
            if not create_new:
                return None
            fex_dict[fex] = {}
        per_fex_dict = fex_dict[fex]
        if 'bp_ports' not in per_fex_dict:
            if not create_new:
                return None
            per_fex_dict['bp_ports'] = {}
        bp_port_dict = per_fex_dict['bp_ports']
    else:
        return None

    slot_id = re.sub('.*slot-', '', (str)(dn_list[2]))
    if slot_id not in bp_port_dict:
        if not create_new:
            return None
        bp_port_dict[slot_id] = {}
    bp_slot_dict = bp_port_dict[slot_id]
    if port_id not in bp_slot_dict:
        if not create_new:
            return None
        bp_slot_dict[port_id] = {}
    per_bp_port_dict = bp_slot_dict[port_id]

    return per_bp_port_dict

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
            if len(port_id) == 1:
                port_id = '0' + port_id
            sub_port_id = ((str)(dn_list[5])).replace('port-', '')
            if len(sub_port_id) == 1:
                sub_port_id = '0' + sub_port_id
            port_id = port_id + '/' + sub_port_id
        else:
            port_id = ((str)(dn_list[4])).replace('port-', '')
            if len(port_id) == 1:
                port_id = '0' + port_id

        if (slot_id + '/' + port_id) not in fi_port_dict:
            fi_port_dict[slot_id + '/' + port_id] = {}
            fi_port_dict[slot_id + '/' + port_id]['channel'] = 'No'
        port_dict = fi_port_dict[slot_id + '/' + port_id]

    return port_dict

def parse_fi_env_stats(domain_ip, top_sys, net_elem, system_stats, fw):
    """
    Use the output of query_classid from UCS to update global stats_dict

    Parameters:
    domain_ip (IP Address of the UCS domain)
    top_sys (managedobjectlist of classid = TopSystem)
    net_elem (managedobjectlist of classid = NetworkElement)
    system_stats (managedobjectlist of classid = SwSystemStats)
    fw (managedobjectlist of classid = FirmwareRunning)

    Returns:
    None

    """

    global stats_dict
    d_dict = stats_dict[domain_ip]

    logger.info('Parse env_stats for {}'.format(domain_ip))
    for item in top_sys:
        logger.debug('In top_sys for {}:{}'.format(domain_ip, item.name))
        d_dict['mode'] = item.mode
        d_dict['name'] = item.name
        uptime_list = (item.system_up_time).split(':')
        uptime = ((int)(uptime_list[0]) * 24 * 60 * 60) + \
                    ((int)(uptime_list[1]) * 60 * 60) + \
                        ((int)(uptime_list[2]) * 60) + (int)(uptime_list[3])
        d_dict['uptime'] = uptime

    for item in net_elem:
        logger.debug('In net_elem for {}:{}'.format(domain_ip, item.dn))
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
        logger.debug('In system_stats for {}:{}'.format(domain_ip, item.dn))
        fi_id = get_fi_id_from_dn(item.dn)
        if fi_id is None:
            logger.error('Unknow FI ID from {}\n{}'.format(domain_ip, item))
            continue
        fi_dict = d_dict[fi_id]
        fi_dict['load'] = item.load
        fi_dict['mem_available'] = item.mem_available

    for item in fw:
        if 'sys/mgmt/fw-system' in item.dn:
            logger.debug('In fw for {}:{}'.format(domain_ip, item.dn))
            d_dict['ucsm_fw_ver'] = item.version
        if 'sys/switch-A/mgmt/fw-system' in item.dn:
            logger.debug('In fw for {}:{}'.format(domain_ip, item.dn))
            d_dict['A']['fi_fw_sys_ver'] = item.version
        if 'sys/switch-B/mgmt/fw-system' in item.dn:
            logger.debug('In fw for {}:{}'.format(domain_ip, item.dn))
            d_dict['B']['fi_fw_sys_ver'] = item.version

    logger.info('Done: Parse env_stats for {}'.format(domain_ip))

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
    d_dict = stats_dict[domain_ip]

    logger.info('Parse fi_stats for {}'.format(domain_ip))
    for item in fcpio:
        logger.debug('In fcpio for {}:{}'.format(domain_ip, item.dn))
        port_dict = get_fi_port_dict(d_dict, item.dn, 'FC')
        if port_dict is None:
            logger.error('Invalid port_dict for {}\n{}'.format(domain_ip,
                                                               item))
            continue
        port_dict['transport'] = 'FC'
        fill_fi_port_common_items(port_dict, item)

    for item in sanpc:
        logger.debug('In sanpc for {}:{}'.format(domain_ip, item.dn))
        port_dict = get_fi_port_dict(d_dict, item.dn, 'FC')
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
        logger.debug('In sanpcep for {}:{}'.format(domain_ip, item.dn))
        port_dict = get_fi_port_dict(d_dict, item.ep_dn, 'FC')
        if port_dict is None:
            logger.error('Invalid port_dict for {}\n{}'.format(domain_ip, item))
            continue
        port_dict['channel'] = ((item.dn.split('/'))[1] + '-' \
                                    +(item.dn.split('/'))[3]).upper()

    # dn: sys/switch-B/slot-1/switch-ether/port-9
    for item in ethpio:
        # ethrx also contains stats for stats of traces between IOM and blades
        # handle them in parse_backplane_port_stats
        if 'switch-' not in item.dn:
            continue
        logger.debug('In ethpio for {}:{}'.format(domain_ip, item.dn))
        port_dict = get_fi_port_dict(d_dict, item.dn, 'Eth')
        if port_dict is None:
            logger.error('Invalid port_dict for {}\n{}'.format(domain_ip,
                                                               item))
            continue
        port_dict['transport'] = 'Eth'
        fill_fi_port_common_items(port_dict, item)
        # peer_dn: sys/chassis-1/slot-2/fabric/port-1 (B)
        # peer_dn: sys/rack-unit-5/adaptor-1/ext-eth-1 (C)
        # peer_dn: sys/chassis-2/slot-1/shared-io-module/fabric/port-4 (S)
        # peer_dn: sys/fex-3/slot-1/fabric/port-1 (F)
        if 'server' in (str)(item.if_role):
            peer_dn_list = (item.peer_dn).split('/')
            peer, peer_type, peer_port = 'unknown', 'unknown', '0/0'
            if 'rack-unit' in (str)(item.peer_dn):
                peer = (str)(peer_dn_list[1])
                peer_type = 'rack'
                slot = (str)(peer_dn_list[-2]).replace('adaptor-','')
                port = ((str)(peer_dn_list[-1])).replace('ext-eth-', '')
                if len(port) == 1:
                    port = '0' + port
                peer_port = slot + '/' + port
            if 'chassis' in (str)(item.peer_dn):
                peer = (str)(peer_dn_list[1])
                peer_type = 'chassis'
                slot = ((str)(peer_dn_list[2])).replace('slot-', '')
                port = ((str)(peer_dn_list[-1])).replace('port-', '')
                if len(port) == 1:
                    port = '0' + port
                peer_port = slot + '/' + port
                # For S-series, chassis format: S-chassis
                if 'shared-io-module' in (str)(item.peer_dn):
                    peer_type = 'S-chassis'
            if 'fex' in (str)(item.peer_dn):
                peer = (str)(peer_dn_list[1])
                peer_type = 'FEX'
                slot = ((str)(peer_dn_list[2])).replace('slot-', '')
                port = ((str)(peer_dn_list[-1])).replace('port-', '')
                if len(port) == 1:
                    port = '0' + port
                peer_port = slot + '/' + port
            port_dict['peer'] = peer
            port_dict['peer_type'] = peer_type
            port_dict['peer_port'] = peer_port

    for item in lanpc:
        logger.debug('In lanpc for {}:{}'.format(domain_ip, item.dn))
        port_dict = get_fi_port_dict(d_dict, item.dn, 'Eth')
        if port_dict is None:
            logger.error('Invalid port_dict for {}\n{}'.format(domain_ip, item))
            continue
        port_dict['transport'] = 'Eth'
        fill_fi_port_common_items(port_dict, item)
        # FabricEthLanPc does not carry correct oper_speed. Use bandwidth
        port_dict['oper_speed'] = get_speed_num_from_string(item.bandwidth, item)

    for item in lanpcep:
        '''
        Populate port-channel information for this port
        Pass ep_dn from FabricEthLanPcEp which contains the dn of the
        physical port.
        For member of a port-channel, set channel=<port_name_of_PC>
        For non-member or a port-channel, set channel=No
        For PC interfaces, do not set channel at all
        '''
        logger.debug('In lanpcep for {}:{}'.format(domain_ip, item.dn))
        port_dict = get_fi_port_dict(d_dict, item.ep_dn, 'Eth')
        if port_dict is None:
            logger.error('Invalid port_dict for {}\n{}'.format(domain_ip, item))
            continue
        port_dict['channel'] = ((item.dn.split('/'))[1] + '-' \
                                    +(item.dn.split('/'))[3]).upper()

    for item in srvpc:
        logger.debug('In srvpc for {}:{}'.format(domain_ip, item.dn))
        port_dict = get_fi_port_dict(d_dict, item.dn, 'Eth')
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
        logger.debug('In srvpcep for {}:{}'.format(domain_ip, item.dn))
        port_dict = get_fi_port_dict(d_dict, item.ep_dn, 'Eth')
        if port_dict is None:
            logger.error('Invalid port_dict for {}\n{}'.format(domain_ip, item))
            continue
        port_dict['channel'] = ((item.dn.split('/'))[3]).upper()

    for item in fcstats:
        logger.debug('In fcstats for {}:{}'.format(domain_ip, item.dn))
        port_dict = get_fi_port_dict(d_dict, item.dn, 'FC')
        if port_dict is None:
            logger.error('Invalid port_dict for {}\n{}'.format(domain_ip, item))
            continue
        port_dict['bytes_rx_delta'] = item.bytes_rx_delta
        port_dict['bytes_tx_delta'] = item.bytes_tx_delta

    for item in fcerr:
        logger.debug('In fcerr for {}:{}'.format(domain_ip, item.dn))
        port_dict = get_fi_port_dict(d_dict, item.dn, 'FC')
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
        logger.debug('In ethrx for {}:{}'.format(domain_ip, item.dn))
        port_dict = get_fi_port_dict(d_dict, item.dn, 'Eth')
        if not port_dict:
            logger.error('Invalid port_dict for {}\n{}'.format(domain_ip, item))
            continue
        port_dict['bytes_rx_delta'] = item.total_bytes_delta

    for item in ethtx:
        # ethtx also contains stats of backplane ports between IOM and blades
        # handle them in parse_backplane_port_stats
        if 'switch-' not in item.dn:
            continue
        logger.debug('In ethtx for {}:{}'.format(domain_ip, item.dn))
        port_dict = get_fi_port_dict(d_dict, item.dn, 'Eth')
        if not port_dict:
            logger.error('Invalid port_dict for {}\n{}'.format(domain_ip, item))
            continue
        port_dict['bytes_tx_delta'] = item.total_bytes_delta

    for item in etherr:
        # Also contains stats of backplane ports between IOM and blades
        # handle them in parse_backplane_port_stats
        if 'switch-' not in item.dn:
            continue
        logger.debug('In etherr for {}:{}'.format(domain_ip, item.dn))
        port_dict = get_fi_port_dict(d_dict, item.dn, 'Eth')
        if not port_dict:
            logger.error('Invalid port_dict for {}\n{}'.format(domain_ip, item))
            continue
        port_dict['out_discard_delta'] = item.out_discard_delta
        port_dict['fcs_delta'] = item.fcs_delta

    for item in ethloss:
        # Also contains stats of backplane ports between IOM and blades
        # handle them in parse_backplane_port_stats
        if 'switch-' not in item.dn:
            continue
        logger.debug('In ethloss for {}:{}'.format(domain_ip, item.dn))
        port_dict = get_fi_port_dict(d_dict, item.dn, 'Eth')
        if not port_dict:
            logger.error('Invalid port_dict for {}\n{}'.format(domain_ip, item))
            continue
        port_dict['giants_delta'] = item.giants_delta

    logger.info('Done: Parse fi_stats for {}'.format(domain_ip))

def parse_compute_inventory(domain_ip, blade, ru):
    """
    Use the output of query_classid from UCS to update global stats_dict

    Parameters:
    domain_ip (IP Address of the UCS domain)
    blade (managedobjectlist as returned by ComputeBlade)
    ru (managedobjectlist as returned by ComputeRackUnit)

    Returns:
    None

    """

    global stats_dict
    d_dict = stats_dict[domain_ip]
    chassis_dict = d_dict['chassis']
    ru_dict = d_dict['ru']

    logger.info('Parse compute blades for {}'.format(domain_ip))

    # dn format: sys/chassis-1/blade-8
    # assigned_to_dn format: org-root/ls-SP-blade-m200-8
    for item in blade:
        logger.debug('In blade for {}:{}'.format(domain_ip, item.dn))
        dn_list = (item.dn).split('/')
        chassis = (str)(dn_list[1])
        blade = (str)(dn_list[2])

        service_profile = re.sub(r"^ls-(.*)", r"\1",(((item.assigned_to_dn).split('/'))[-1]))
        if 'none' in item.association or len(service_profile) == 0:
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

    logger.info('Parse rack units for {}'.format(domain_ip))

    # dn format: sys/rack-unit-2
    # assigned_to_dn format: org-root/org-HX3AF240b/ls-rack-unit-8
    for item in ru:
        logger.debug('In fill_per_ru for {}:{}'.format(domain_ip, item.dn))
        dn_list = (item.dn).split('/')
        ru = (str)(dn_list[-1])
        service_profile = re.sub(r"^ls-(.*)", r"\1",(((item.assigned_to_dn).split('/'))[-1]))
        if 'none' in item.association or len(service_profile) == 0:
            service_profile = 'Unknown'
        # Numbers might be handy in front-end representations/color coding
        if 'ok' in item.oper_state:
            oper_state_code = 0
        else:
            oper_state_code = 1

        if ru not in ru_dict:
            ru_dict[ru] = {}
        per_ru_dict = ru_dict[ru]

        per_ru_dict['service_profile'] = service_profile
        per_ru_dict['association'] = item.association
        per_ru_dict['oper_state'] = item.oper_state
        per_ru_dict['oper_state_code'] = oper_state_code
        per_ru_dict['operability'] = item.operability
        per_ru_dict['admin_state'] = item.admin_state
        per_ru_dict['model'] = item.model
        per_ru_dict['num_cores'] = item.num_of_cores
        per_ru_dict['num_cpus'] = item.num_of_cpus
        per_ru_dict['memory'] = item.available_memory
        per_ru_dict['serial'] = item.serial
        per_ru_dict['num_adaptors'] = item.num_of_adaptors
        per_ru_dict['num_vEths'] = item.num_of_eth_host_ifs
        per_ru_dict['num_vFCs'] = item.num_of_fc_host_ifs

    logger.info('Done: Parse rack units for {}'.format(domain_ip))

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
    d_dict = stats_dict[domain_ip]
    ru_dict = d_dict['ru']

    logger.info('Parse vnic_stats for {}'.format(domain_ip))
    for item in host_fcif:
        logger.debug('In host_fcif for {}:{}'.format(domain_ip, item.dn))
        if 'rack-unit' in item.dn:
            fill_ru_dict(item, ru_dict)
        else:
            fill_chassis_dict(item, domain_ip)

    for item in host_ethif:
        logger.debug('In host_ethif for {}:{}'.format(domain_ip, item.dn))
        if 'rack-unit' in item.dn:
            fill_ru_dict(item, ru_dict)
        else:
            fill_chassis_dict(item, domain_ip)

    '''
    DcxVC contains pinned uplink port. If oper_border_port_id == 0, discard
    if oper_border_slot_id, it is a port-channel
    else, a physical port
    dn format: sys/chassis-1/blade-2/fabric-A/path-1/vc-1355
    dn format: sys/rack-unit-5/fabric-B/path-1/vc-1324
    Important: Even though dcxvc contains fi_id, do not use it. Fill fi_id from
    host_ethif or host_fcif due to failover scenario and active VC
    '''

    for item in dcxvc:
        if item.vnic == '' or (int)(item.oper_border_port_id) == 0:
            continue

        logger.debug('In dcxvc for {}:{}'.format(domain_ip, item.dn))
        vif_name = item.vnic

        vif_dict = get_vif_dict_from_dn(domain_ip, item.dn)
        if vif_dict is None:
            continue
        per_vif_dict = vif_dict[vif_name]
        if per_vif_dict is None:
            continue
        if per_vif_dict['fi_id'] != item.switch_id:
            logger.debug('Ignoring inactive dcxvc for {}'.format(item.dn))
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

        per_vif_dict['pinned_fi_uplink'] = pinned_uplink
        if 'fc' in item.transport:
            per_vif_dict['bound_vfc'] = 'vfc' + (str)(item.id)
            per_vif_dict['bound_veth'] = 'veth' + (str)(item.fcoe_id)
        if 'ether' in item.transport:
            per_vif_dict['bound_veth'] = 'veth' + (str)(item.id)

    # dn format: sys/chassis-1/blade-2/adaptor-1/host-fc-4/vnic-stats
    # dn format: sys/rack-unit-5/adaptor-1/host-eth-6/vnic-stats
    for item in vnic_stats:
        logger.debug('In vnic_stats for {}:{}'.format(domain_ip, item.dn))
        if (item.dn).startswith('vmm'):
            logger.info('VM-FEX not supported. Skipping. {}:{}'. \
                        format(domain_ip, item.dn))
            continue
        rn = (((str)(item.dn)).split('/'))[-2]
        vif_dict = get_vif_dict_from_dn(domain_ip, item.dn)
        if vif_dict is None:
            logger.debug('vif_dict is None')
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
    # dn:sys/chassis-1/slot-2/host/port-30
    # dn:sys/fex-3/slot-1/host/port-29
    # ep_dn: sys/chassis-1/slot-2/host/pc-1285
    for item in srv_fio:
        logger.debug('In srv_fio for {}:{}, peer_dn:{}'. \
                    format(domain_ip, item.dn, item.peer_dn))
        port_dict = get_bp_port_dict_from_dn(domain_ip, item.dn, True)
        if port_dict is None:
            logger.error('Invalid bp_port_dict for {}:{}' \
                         .format(domain_ip, item))
            continue
        port_dict['fi_id'] = item.switch_id
        port_dict['admin_state'] = item.admin_state
        port_dict['admin_speed'] = \
                        get_speed_num_from_string(item.admin_speed, item)
        port_dict['oper_state'] = item.oper_state
        port_dict['channel'] = 'No'

        # peer_dn: sys/chassis-1/blade-8/adaptor-2/ext-eth-5
        # peer_dn: sys/rack-unit-2/adaptor-1/ext-eth-2
        # peer_dn: sys/chassis-17/slot-2/shared-io-module/fabric/port-5
        peer_dn_list = (item.peer_dn).split('/')
        if len(peer_dn_list) < 3:
            if 'up' in (str)(item.oper_state):
                logger.warning('oper_state up still unable to decode ' \
                    'peer_dn:{}, dn:{}'.format(item.peer_dn, item.dn))
            else:
                logger.debug('Unable to decode peer_dn:{}, dn:{}, ' \
                'oper_state:{}'.format(item.peer_dn, item.dn, item.oper_state))
        else:
            peer, peer_type, peer_port = 'unknown', 'unknown', '0/0'
            # FEX connected to rack-unit
            if 'rack-unit' in (str)(item.peer_dn):
                peer = (str)(peer_dn_list[-3])
                peer_type = 'rack'
                slot = ((str)(peer_dn_list[-2])).replace('adaptor-', '')
                port = ((str)(peer_dn_list[-1])).replace('ext-eth-', '')
                if len(port) == 1:
                    port = '0' + port
                peer_port = slot + '/' + port
            # IOM within 5108 chassis
            if 'chassis' in (str)(item.peer_dn):
                peer = (str)(peer_dn_list[-3])
                peer_type = 'blade'
                slot = ((str)(peer_dn_list[-2])).replace('adaptor-', '')
                port = ((str)(peer_dn_list[-1])).replace('ext-eth-', '')
                if len(port) == 1:
                    port = '0' + port
                peer_port = slot + '/' + port
                # FEX Connected to S-series
                if 'shared-io-module' in (str)(item.peer_dn):
                    peer = (str)(peer_dn_list[1])
                    peer_type = 'S-chassis'
                    slot = ((str)(peer_dn_list[2])).replace('slot-', '')
                    port = ((str)(peer_dn_list[-1])).replace('port-', '')
                    if len(port) == 1:
                        port = '0' + port
                    peer_port = slot + '/' + port
            port_dict['peer'] = peer
            port_dict['peer_type'] = peer_type
            port_dict['peer_port'] = peer_port

    # dn format: sys/chassis-1/slot-1/host/pc-1290
    for item in srv_fiopc:
        logger.debug('In srv_fiopc for {}:{}'.format(domain_ip, item.dn))
        port_dict = get_bp_port_dict_from_dn(domain_ip, item.dn, True)
        if port_dict is None:
            logger.error('Invalid bp_port_dict for {}:{}' \
                         .format(domain_ip, item))
            continue
        port_dict['fi_id'] = item.switch_id
        port_dict['oper_speed'] = \
                        get_speed_num_from_string(item.oper_speed, item)
        port_dict['oper_state'] = item.oper_state

    # dn format: sys/chassis-1/slot-1/host/pc-1290/ep-slot-1-port-27
    for item in srv_fiopcep:
        logger.debug('In srv_fiopcep for {}:{}'.format(domain_ip, item.dn))
        '''
        Populate port-channel information for this port
        Passon ep_dn from EtherServerIntFIoPcEp which contains the dn of the
        backplane port.
        For member of a port-channel, set channel=<port_name_of_PC>
        For non-member or a port-channel, set channel=No
        For PC interfaces, do not set channel at all
        '''

        port_dict = get_bp_port_dict_from_dn(domain_ip, item.ep_dn, False)
        if port_dict is None:
            logger.error('Invalid bp_port_dict for {}:{}' \
                         .format(domain_ip, item))
            continue
        port_dict['channel'] = (((item.dn).split('/'))[4]).upper()

    # dn format: sys/chassis-2/slot-1/host/port-29/rx-stats
    for item in ethrx:
        # ethrx also contains stats of FI ports. Handle them with FI ports
        if 'chassis-' in item.dn or 'fex' in item.dn:
            logger.debug('In ethrx for {}:{}'.format(domain_ip, item.dn))
            port_dict = get_bp_port_dict_from_dn(domain_ip, item.dn, False)
            if port_dict is None:
                logger.error('Invalid bp_port_dict for {}:{}' \
                         .format(domain_ip, item))
                continue
            port_dict['bytes_rx_delta'] = item.total_bytes_delta

    # dn format: sys/chassis-2/slot-1/host/port-29/tx-stats
    for item in ethtx:
        # ethrx also contains stats of FI ports. Handle them with FI ports
        if 'chassis-' in item.dn or 'fex' in item.dn:
            logger.debug('In ethtx for {}:{}'.format(domain_ip, item.dn))
            port_dict = get_bp_port_dict_from_dn(domain_ip, item.dn, False)
            if port_dict is None:
                logger.error('Invalid bp_port_dict for {}:{}' \
                         .format(domain_ip, item))
                continue
            port_dict['bytes_tx_delta'] = item.total_bytes_delta

    for item in etherr:
        # etherr also contains stats of FI ports. Handle them with FI ports
        if 'chassis-' in item.dn or 'fex' in item.dn:
            logger.debug('In etherr for {}:{}'.format(domain_ip, item.dn))
            port_dict = get_bp_port_dict_from_dn(domain_ip, item.dn, False)
            if not port_dict:
                logger.error('Invalid port_dict for {}:{}'.format(domain_ip, item))
                continue
            port_dict['out_discard_delta'] = item.out_discard_delta
            port_dict['fcs_delta'] = item.fcs_delta

    '''
    Following code looks up FabricPathEp to find mapping between IOM backplane
    port and FI server ports (which is connected to IOM fabric port)
    Go through it twice, first when locale is chassis and again when locale is
    server
    Read the documentation to understand the logic. It takes a while ...
    '''

    path_dict = {}
    for item in pathep:
        logger.debug('In pathep for {}:{}'.format(domain_ip, item.dn))
        if 'fex' in item.dn:
            continue
        # dn format: sys/chassis-1/blade-3/fabric-A/path-1/ep-mux
        # peer_dn format: sys/chassis-1/slot-2/host/port-5
        if item.locale == 'chassis' and 'blade' in item.dn:
            dn_list = (item.dn).split('/')
            peer_dn_list = (item.peer_dn).split('/')
            #fi_id = (dn_list[3]).replace('fabric-', '')
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
        logger.debug('In pathep - 2 for {}:{}'.format(domain_ip, item.dn))
        if 'fex' in item.dn:
            continue
        # dn format: sys/chassis-1/blade-3/fabric-A/path-1/ep-mux-fabric
        # peer_dn format: sys/switch-A/slot-1/switch-ether/port-3
        if item.locale == 'server' and 'blade' in item.dn:
            dn_list = (item.dn).split('/')
            peer_dn_list = (item.peer_dn).split('/')
            #fi_id = (dn_list[3]).replace('fabric-', '')
            chassis = dn_list[1]
            path = chassis + '/' + dn_list[2] + '/' + dn_list[3] + \
                                '/' + dn_list[4]
            if path not in path_dict:
                continue
            # Construct a DN in sys/chassis-2/slot-1/host/port-29/tx-stats
            # format from slot and port in path_dict
            dn_for_port_dict = 'sys/' + chassis + '/' + path_dict[path]
            port_dict = get_bp_port_dict_from_dn(domain_ip, dn_for_port_dict, False)
            if port_dict is None:
                logger.warning('Invalid bp_port_dict for {}:{}' \
                             .format(domain_ip, item.dn))
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

    if user_args.get('raw_dump'):
        current_log_level = logger.level
        logger.setLevel(logging.DEBUG)
        logger.info('Printing raw dump')
        for domain_ip, rsp in raw_sdk_stats.items():
            for class_id, obj in rsp.items():
                for items in obj:
                    logger.debug('{} :\n{}'. \
                        format(domain_ip, items))
        logger.info('Printing raw dump - DONE')
        logger.setLevel(current_log_level)

    for domain_ip, obj in raw_sdk_stats.items():
        logger.info('Start parsing SDK stats for {}'.format(domain_ip))
        # There is a strange issue where every 2 hours come of the UCS doamins
        # do not return anything, resulting in empty obj dict. Check for the
        # condition to avoid KeyError exception. Log it properly
        if Counter(class_ids) != Counter(obj.keys()):
            logger.error('Missing returned class ID(s) from {}. Skipping...' \
                         'Value:\n{}'.format(domain_ip, obj))
            continue

        try:
            parse_fi_env_stats(domain_ip,
                               obj['TopSystem'],
                               obj['NetworkElement'],
                               obj['SwSystemStats'],
                               obj['FirmwareRunning'])
        except Exception as e:
            s = ''
            for item in obj['TopSystem']:
                s = s + (str)(item)
            for item in obj['NetworkElement']:
                s = s + (str)(item)
            for item in obj['SwSystemStats']:
                s = s + (str)(item)
            for item in obj['FirmwareRunning']:
                s = s + (str)(item)
            logger.exception('parse_fi_env_stats:{}\n{}'.format(e, s))

        try:
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
        except Exception as e:
            s = ''
            for item in obj['FcPIo']:
                s = s + (str)(item)
            for item in obj['FabricFcSanPc']:
                s = s + (str)(item)
            for item in obj['FabricFcSanPcEp']:
                s = s + (str)(item)
            for item in obj['FcStats']:
                s = s + (str)(item)
            for item in obj['FcErrStats']:
                s = s + (str)(item)
            for item in obj['EtherPIo']:
                s = s + (str)(item)
            for item in obj['FabricEthLanPc']:
                s = s + (str)(item)
            for item in obj['FabricEthLanPcEp']:
                s = s + (str)(item)
            for item in obj['EtherRxStats']:
                s = s + (str)(item)
            for item in obj['EtherTxStats']:
                s = s + (str)(item)
            for item in obj['EtherErrStats']:
                s = s + (str)(item)
            for item in obj['EtherLossStats']:
                s = s + (str)(item)
            for item in obj['FabricDceSwSrvPc']:
                s = s + (str)(item)
            for item in obj['FabricDceSwSrvPcEp']:
                s = s + (str)(item)
            logger.exception('parse_fi_stats:{}\n{}'.format(e, s))

        try:
            parse_compute_inventory(domain_ip,
                                    obj['ComputeBlade'],
                                    obj['ComputeRackUnit'])
        except Exception as e:
            s = ''
            for item in obj['ComputeBlade']:
                s = s + (str)(item)
            for item in obj['ComputeRackUnit']:
                s = s + (str)(item)
            logger.exception('parse_compute_inventory:{}\n{}'.format(e, s))

        try:
            parse_backplane_port_stats(domain_ip,
                                       obj['EtherServerIntFIo'],
                                       obj['EtherServerIntFIoPc'],
                                       obj['EtherServerIntFIoPcEp'],
                                       obj['EtherRxStats'],
                                       obj['EtherTxStats'],
                                       obj['EtherErrStats'],
                                       obj['EtherLossStats'],
                                       obj['FabricPathEp'])
        except Exception as e:
            s = ''
            for item in obj['EtherServerIntFIo']:
                s = s + (str)(item)
            for item in obj['EtherServerIntFIoPc']:
                s = s + (str)(item)
            for item in obj['EtherServerIntFIoPcEp']:
                s = s + (str)(item)
            for item in obj['EtherRxStats']:
                s = s + (str)(item)
            for item in obj['EtherTxStats']:
                s = s + (str)(item)
            for item in obj['EtherErrStats']:
                s = s + (str)(item)
            for item in obj['EtherLossStats']:
                s = s + (str)(item)
            for item in obj['FabricPathEp']:
                s = s + (str)(item)
            logger.exception('parse_backplane_port_stats:{}\n{}'.format(e, s))

        try:
            parse_vnic_stats(domain_ip,
                             obj['AdaptorVnicStats'],
                             obj['AdaptorHostEthIf'],
                             obj['AdaptorHostFcIf'],
                             obj['DcxVc'])
        except Exception as e:
            s = ''
            for item in obj['AdaptorVnicStats']:
                s = s + (str)(item)
            for item in obj['AdaptorHostEthIf']:
                s = s + (str)(item)
            for item in obj['AdaptorHostFcIf']:
                s = s + (str)(item)
            for item in obj['DcxVc']:
                s = s + (str)(item)
            logger.exception('parse_vnic_stats:{}\n{}'.format(e, s))

def parse_pfc_stats(pfc_output, domain_ip, fi_id):
    """
    Parse PFC stats

    Here is a sample output of the command
    ============================================================
    Port               Mode Oper(VL bmap)  RxPPP      TxPPP
    ============================================================

    Ethernet1/1        Auto Off           0          0
    Vethernet9547      Auto Off           0          0
    Ethernet1/1/2      Auto Off           0          0
    Ethernet7/1/33     Auto Off           0          0
    Br-Ethernet1/17/1  Auto Off           373112640  5422273

    backplane port between IOM/FEX and server port
    is reported in x/y/z format, where
    x is chassis_id/fex_id, y is always 1 and z is the backplane port
    on IOM/FEX. For 5108 chassis, this output does not tell the slot ID
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
    d_dict = stats_dict[domain_ip]
    fi_port_dict = d_dict[fi_id]['fi_ports']
    chassis_dict = d_dict['chassis']
    fex_dict = d_dict['fex']
    iom_slot_id = 0 # Invalid. Update it later
    chassis_id = 0 # Invalid. Update it later
    if 'model' in d_dict[fi_id]:
        fi_model = d_dict[fi_id]['model']
    else:
        fi_model = 'unknown'

    logger.info('Parse pause stats for {}, {}'.format(domain_ip, fi_model))
    logger.debug('{} - FI-{} - show interface priority\n{}\n'. \
                 format(domain_ip, fi_id, pfc_output))
    pfc_op = pfc_output.splitlines()
    for lines in pfc_op:
        line = lines.split()
        # skip the Vethernet
        if len(line) < 5 or line[0].startswith('Veth'):
            continue

        port_list = line[0].split('/')
        if line[0].startswith('Eth') and len(port_list) == 2:
            # port on FI
            # line[0] is port name, -2 is RX, -1 is TX PFC stats
            # Ethernet1/3        Auto Off           2          0
            slot_id = (port_list[0]).replace('Ethernet', '')
            port_id = port_list[1]
            # Prefix single digit port number with 0 to help sorting
            if len(port_id) == 1:
                port_id = '0' + port_id
            key = slot_id + '/' + port_id
            if key not in fi_port_dict:
                logger.debug('{} not found in fi_port_dict for {}'. \
                            format(key, domain_ip))
                # On UCS mini, FI (server) ports are bp_ports on chassis-1
                if 'UCS-FI-M-' in fi_model:
                    c_id = '1'
                    chassis_id = 'chassis-' + c_id
                    # Do not continue if chassis_dict is not initialized
                    # Something else might be wrong
                    if chassis_id in chassis_dict:
                        logger.debug('M - Found {} in {}'.format(key, \
                                                                 chassis_id))
                        per_chassis_dict = chassis_dict[chassis_id]
                        if 'bp_ports' not in per_chassis_dict:
                            logger.warning('...but not bp_ports')
                            continue
                        bp_port_dict = per_chassis_dict['bp_ports']
                    else:
                        logger.warning('M - Unable to find chassis {}'. \
                                        format(chassis_id))
                        continue
                    for iom_slot, port_dict in bp_port_dict.items():
                        for iom_port, per_bp_port_dict in port_dict.items():
                            # Do not run this loop more than once. All ports of
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
                    logger.debug('M - {} in {} IOM port {}/{}'. \
                                format(domain_ip, chassis_id, iom_slot_id, \
                                       port_id))
                    per_bp_port_dict['pause_rx'] = line[-2]
                    per_bp_port_dict['pause_tx'] = line[-1]
                continue
            logger.debug('FI port {}:{}:{}'.format(key, domain_ip, fi_id))
            fi_port_dict[key]['pause_rx'] = line[-2]
            fi_port_dict[key]['pause_tx'] = line[-1]
        elif line[0].startswith('Br-') and len(port_list) == 3:
            # Breakout port on FI
            # line[0] is port name, -2 is RX, -1 is TX PFC stats
            # Br-Ethernet1/17/1  Auto Off           373112640  5422273
            slot_id = (port_list[0]).replace('Br-Ethernet', '')
            port_id = port_list[1]
            # Prefix single digit port number with 0 to help sorting
            if len(port_id) == 1:
                port_id = '0' + port_id
            sub_port_id = port_list[2]
            if len(sub_port_id) == 1:
                sub_port_id = '0' + sub_port_id
            port_id = port_id + '/' + sub_port_id
            key = slot_id + '/' + port_id
            if key not in fi_port_dict:
                logger.debug('{} not found in fi_port_dict for {}'. \
                            format(key, domain_ip))
                continue
            logger.debug('FI port {}:{}:{}'.format(key, domain_ip, fi_id))
            fi_port_dict[key]['pause_rx'] = line[-2]
            fi_port_dict[key]['pause_tx'] = line[-1]
        elif line[0].startswith('Eth') and len(port_list) == 3:
            c_id = (port_list[0]).replace('Ethernet', '')
            chassis_id = 'chassis-' + c_id
            fex_id = 'fex-' + c_id
            # Do not continue if chassis_dict is not initialized
            # Something else might be wrong
            if chassis_id in chassis_dict:
                logger.debug('Found {}'.format(chassis_id))
                per_chassis_dict = chassis_dict[chassis_id]
                if 'bp_ports' not in per_chassis_dict:
                    logger.warning('...but not bp_ports')
                    continue
                bp_port_dict = per_chassis_dict['bp_ports']
            elif fex_id in fex_dict:
                logger.debug('Found {}'.format(fex_id))
                per_fex_dict = fex_dict[fex_id]
                if 'bp_ports' not in per_fex_dict:
                    logger.warning('...but not bp_ports')
                    continue
                bp_port_dict = per_fex_dict['bp_ports']
            else:
                logger.warning('Unable to find chassis or FEX with id {}'. \
                                format(c_id))
                continue
            for iom_slot, port_dict in bp_port_dict.items():
                for iom_port, per_bp_port_dict in port_dict.items():
                    # Do not run this loop more than once. All ports of
                    # a IOM/FEX are expected to carry same fi_id
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
            logger.debug('IOM/FEX port {}:{}:{}:{}'.format(domain_ip, \
                         c_id, iom_slot_id, port_id))
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

    if user_args.get('no_ssh'):
        logger.info('Skipping parsing of CLI metrics due to --no-ssh flag')
        return

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

###############################################################################
# END: Parser functions
###############################################################################

###############################################################################
# BEGIN: Output functions
###############################################################################

def influxdb_lp_server_fields(server_dict, server_fields):
    server_fields = server_fields + \
        'admin_state="' + server_dict['admin_state'] + '"' + \
        ',association="' + server_dict['association'] + '"' + \
        ',operability="' + server_dict['operability'] + '"' + \
        ',oper_state="' + server_dict['oper_state'] + '"' + \
        ',oper_state_code=' + \
                (str)(server_dict['oper_state_code']) + \
        ',memory=' + server_dict['memory'] + \
        ',model="' + server_dict['model'] + '"' + \
        ',num_adaptors=' + server_dict['num_adaptors'] + \
        ',num_cores=' + server_dict['num_cores'] + \
        ',num_cpus=' + server_dict['num_cpus'] + \
        ',num_vEths=' + server_dict['num_vEths'] + \
        ',num_vFCs=' + server_dict['num_vFCs'] + \
        ',serial="' + server_dict['serial'] + '"'

    server_fields = server_fields + '\n'

    return server_fields

def influxdb_lp_vnic(per_vif_dict, vnic_tags, vnic_fields):
    if 'peer_type' in per_vif_dict:
        if per_vif_dict['peer_type'] != 'unknown':
            vnic_tags = vnic_tags + ',peer_type=' + \
                per_vif_dict['peer_type'] + \
                ',peer=' + per_vif_dict['peer'] + \
                ',peer_port=' + per_vif_dict['peer_port']
    if 'fi_id' in per_vif_dict:
        vnic_tags = vnic_tags + ',fi_id=' + \
                        per_vif_dict['fi_id']
    if 'pinned_fi_uplink' in per_vif_dict:
        vnic_tags = vnic_tags + ',pinned_uplink=' + \
            per_vif_dict['pinned_fi_uplink']
    if 'bound_vfc' in per_vif_dict:
        vnic_tags = vnic_tags + \
            ',bound_vfc=' + per_vif_dict['bound_vfc']
    if 'bound_veth' in per_vif_dict:
        vnic_tags = vnic_tags + \
            ',bound_veth=' + per_vif_dict['bound_veth']

    if 'bytes_rx_delta' in per_vif_dict:
        vnic_fields = vnic_fields + 'bytes_rx_delta=' + \
                        per_vif_dict['bytes_rx_delta']
    if 'bytes_tx_delta' in per_vif_dict:
        vnic_fields = vnic_fields + ',bytes_tx_delta=' + \
                        per_vif_dict['bytes_tx_delta']
    if 'errors_rx_delta' in per_vif_dict:
        vnic_fields = vnic_fields + ',errors_rx_delta=' + \
                        per_vif_dict['errors_rx_delta']
    if 'errors_tx_delta' in per_vif_dict:
        vnic_fields = vnic_fields + ',errors_tx_delta=' + \
                        per_vif_dict['errors_tx_delta']
    if 'dropped_rx_delta' in per_vif_dict:
        vnic_fields = vnic_fields + ',dropped_rx_delta=' + \
                        per_vif_dict['dropped_rx_delta']
    if 'dropped_tx_delta' in per_vif_dict:
        vnic_fields = vnic_fields + ',dropped_tx_delta=' + \
                        per_vif_dict['dropped_tx_delta']

    vnic_fields = vnic_fields + '\n'

    return (vnic_tags, vnic_fields)

def influxdb_lp_bp_ports(per_bp_port_dict, bp_tags, bp_fields):
    if 'peer_type' in per_bp_port_dict:
       if per_bp_port_dict['peer_type'] != 'unknown':
            bp_tags = bp_tags + ',peer_type=' + \
                    per_bp_port_dict['peer_type'] + \
                    ',peer=' + per_bp_port_dict['peer'] + \
                    ',peer_port=' + per_bp_port_dict['peer_port']
    if 'channel' in per_bp_port_dict:
        bp_tags = bp_tags + ',channel=' + \
                    per_bp_port_dict['channel']
    if 'fi_server_port' in per_bp_port_dict:
        if per_bp_port_dict['fi_server_port'] == '':
            bp_tags = bp_tags + ',fi_server_port=unknown'
        else:
            bp_tags = bp_tags + ',fi_server_port=' + \
                    per_bp_port_dict['fi_server_port']


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
    if 'out_discard_delta' in per_bp_port_dict:
        bp_fields = bp_fields + \
                    ',out_discard_delta='+\
                        per_bp_port_dict['out_discard_delta']
    if 'fcs_delta' in per_bp_port_dict:
        bp_fields = bp_fields + \
                    ',fcs_delta='+\
                        per_bp_port_dict['fcs_delta']
    bp_fields = bp_fields + '\n'

    return (bp_tags, bp_fields)

'''
InfluxDB Line Protocol Reference
* Never double or single quote the timestamp
* Never single quote field values
* Do not double or single quote measurement names, tag keys, tag values,
    and field keys
* Do not double quote field values that are floats, integers, or Booleans
* Do double quote field values that are strings
* Performance tips: sort by tag key
'''
def print_output_in_influxdb_lp():
    global stats_dict
    final_print_string = ''
    fi_id_list = ['A', 'B']

    server_prefix = 'Servers,domain='
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
                    ',ucsm_fw_ver="' + d_dict['ucsm_fw_ver'] + '"' + \
                    ',fi_fw_sys_ver="' + fi_dict['fi_fw_sys_ver'] + '"' + \
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
            - Only server ports have peer_chassis, peer_iom_slot, peer_iom_port
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
                fi_port_tags = fi_port_tags + 'fi_id=' + fi_id
                if 'channel' in per_fi_port_dict:
                    fi_port_tags = fi_port_tags + ',channel=' + \
                                    per_fi_port_dict['channel']
                fi_port_tags = fi_port_tags + ',location=' + location
                if 'peer_type' in per_fi_port_dict:
                    if per_fi_port_dict['peer_type'] != 'unknown':
                        fi_port_tags = fi_port_tags + ',peer_type=' + \
                                per_fi_port_dict['peer_type'] + \
                                ',peer=' + per_fi_port_dict['peer'] + \
                                ',peer_port=' + per_fi_port_dict['peer_port']

                fi_port_tags = fi_port_tags + ',port=' + fi_port + \
                                ',transport=' + per_fi_port_dict['transport']

                fi_port_fields = fi_port_fields + \
                'admin_state="' + per_fi_port_dict['admin_state'] + '",' + \
                'description="' + per_fi_port_dict['name'] + '",' + \
                'oper_speed=' + (str)(per_fi_port_dict['oper_speed']) + ',' + \
                'oper_state="' + (str)(per_fi_port_dict['oper_state']) + '"'

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
                if 'out_discard_delta' in per_fi_port_dict:
                    fi_port_fields = fi_port_fields + ',out_discard_delta='+\
                                    per_fi_port_dict['out_discard_delta']
                if 'fcs_delta' in per_fi_port_dict:
                    fi_port_fields = fi_port_fields + ',fcs_delta='+\
                                    per_fi_port_dict['fcs_delta']
                # Ports will role server goes in FIServerPortStats, rest all
                # ports go into FIUplinkPortStats, including unknown
                if per_fi_port_dict['if_role'] == 'server':
                    fi_port_prefix = fi_server_port_prefix
                else:
                    fi_port_prefix = fi_uplink_port_prefix

                fi_port_prefix = fi_port_prefix + domain_ip
                fi_port_fields = fi_port_fields + '\n'
                final_print_string = final_print_string + fi_port_prefix + \
                                        fi_port_tags + fi_port_fields
            # Done: Build insert string FIServerPortStats and FIUplinkPortStats

        # Build insert string for blade servers - Servers, Vnic, Backplane, etc.
        chassis_dict = d_dict['chassis']
        for chassis_id, per_chassis_dict in chassis_dict.items():
            if 'blades' not in per_chassis_dict:
                continue
            blade_dict = per_chassis_dict['blades']
            for blade_id, per_blade_dict in blade_dict.items():
                # Build insert string for BladeServers
                blade_prefix = server_prefix + domain_ip
                blade_tags = ','
                blade_fields = ' '
                blade_tags = blade_tags + 'chassis=' + chassis_id + \
                            ',id=' + blade_id + \
                            ',location=' + location + \
                            ',service_profile=' + \
                                per_blade_dict['service_profile'] + \
                            ',type=' + 'blade'

                blade_fields = influxdb_lp_server_fields(per_blade_dict, \
                                                         blade_fields)

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
                if 'adaptors' not in per_blade_dict:
                    continue
                adaptor_dict = per_blade_dict['adaptors']
                # Build insert string for VnicStats
                for adaptor_id, per_adaptor_dict in adaptor_dict.items():
                    if 'vifs' not in per_adaptor_dict:
                        continue
                    vif_dict = per_adaptor_dict['vifs']
                    for vif_name, per_vif_dict in vif_dict.items():
                        if 'up' not in per_vif_dict['link_state']:
                            continue
                        v_prefix = vnic_prefix + domain_ip
                        vnic_tags = ','
                        vnic_fields = ' '
                        vnic_tags = vnic_tags + 'adaptor=' + adaptor_id + \
                            ',chassis=' + chassis_id + \
                            ',domain_name=' + name + \
                            ',location=' + location + \
                            ',server=' + blade_id + \
                            ',service_profile=' + \
                                    per_blade_dict['service_profile'] + \
                            ',transport=' + per_vif_dict['transport'] + \
                            ',vif_name=' + vif_name

                        vnic_tags, vnic_fields = \
                        influxdb_lp_vnic(per_vif_dict, vnic_tags, vnic_fields)
                        final_print_string = final_print_string + v_prefix \
                                                + vnic_tags + vnic_fields
                # Done: Build insert string for VnicStats

            # Build insert string for BackplanePortStats
            if 'bp_ports' not in per_chassis_dict:
                continue
            bp_port_dict = per_chassis_dict['bp_ports']
            for iom_slot_id, iom_slot_dict in bp_port_dict.items():
                for bp_port_id, per_bp_port_dict in iom_slot_dict.items():
                    bp_prefix = bp_port_prefix + domain_ip
                    bp_tags = ','
                    bp_fields = ' '
                    bp_tags = bp_tags + 'bp_port=' + iom_slot_id + '/' + \
                        bp_port_id + ',chassis=' + chassis_id + \
                        ',fi_id=' + per_bp_port_dict['fi_id']
                    if 'peer_type' in per_bp_port_dict:
                        if per_bp_port_dict['peer_type'] != 'unknown':
                            bp_tags = bp_tags + ',peer_type=' + \
                                per_bp_port_dict['peer_type'] + \
                                ',peer=' + per_bp_port_dict['peer'] + \
                                ',peer_port=' + per_bp_port_dict['peer_port']
                        per_blade_dict = \
                                blade_dict[per_bp_port_dict['peer']]
                        bp_tags = bp_tags + ',peer_service_profile=' + \
                                    per_blade_dict['service_profile']
                    bp_tags, bp_fields = \
                                influxdb_lp_bp_ports(per_bp_port_dict, \
                                                     bp_tags, bp_fields)

                    final_print_string = final_print_string + bp_prefix \
                                            + bp_tags + bp_fields
            # Done: Build insert string for BackplanePortStats

        # Build insert string for rack servers - Servers, Vnic, Backplane, etc.
        ru_dict = d_dict['ru']
        for ru_id, per_ru_dict in ru_dict.items():
            # Build insert string for Rack Servers
            rack_prefix = server_prefix + domain_ip
            rack_tags = ','
            rack_fields = ' '
            rack_tags = rack_tags + 'service_profile=' + \
                            per_ru_dict['service_profile'] + \
                        ',location=' + location + \
                        ',id=' + ru_id + \
                        ',type=' + 'rack'

            rack_fields = influxdb_lp_server_fields(per_ru_dict, \
                                                     rack_fields)

            final_print_string = final_print_string + \
                rack_prefix + rack_tags + rack_fields
            # Done: Build insert string for Rack Servers

            # This is a strict check before going any deeper. Candidate
            # for re-visit. If this check is removed, check the presence
            # of keys in VnicState before filling in the values because
            # keys may be missing like fi_id, uplink_port, etc.
            if 'ok' not in per_ru_dict['oper_state'] or \
                'associated' not in per_ru_dict['association']:
                continue
            if 'adaptors' not in per_ru_dict:
                continue
            adaptor_dict = per_ru_dict['adaptors']
            # Build insert string for VnicStats
            for adaptor_id, per_adaptor_dict in adaptor_dict.items():
                if 'vifs' not in per_adaptor_dict:
                    continue
                vif_dict = per_adaptor_dict['vifs']
                for vif_name, per_vif_dict in vif_dict.items():
                    if 'up' not in per_vif_dict['link_state']:
                        continue
                    v_prefix = vnic_prefix + domain_ip
                    vnic_tags = ','
                    vnic_fields = ' '
                    vnic_tags = vnic_tags + 'adaptor=' + adaptor_id + \
                        ',server=' + ru_id + ',chassis=' + ru_id + \
                        ',domain_name=' + name + ',service_profile=' + \
                        per_ru_dict['service_profile'] + \
                        ',transport=' + per_vif_dict['transport'] + \
                        ',vif_name=' + vif_name + \
                        ',location=' + location
                    vnic_tags, vnic_fields = \
                    influxdb_lp_vnic(per_vif_dict, vnic_tags, vnic_fields)
                    final_print_string = final_print_string + v_prefix \
                                            + vnic_tags + vnic_fields
            # Done: Build insert string for VnicStats

        # Build insert string for FEX
        fex_dict = d_dict['fex']
        for fex_id, per_fex_dict in fex_dict.items():
            # Build insert string for BackplanePortStats
            if 'bp_ports' not in per_fex_dict:
                continue
            bp_port_dict = per_fex_dict['bp_ports']
            for iom_slot_id, iom_slot_dict in bp_port_dict.items():
                for bp_port_id, per_bp_port_dict in iom_slot_dict.items():
                    bp_prefix = bp_port_prefix + domain_ip
                    bp_tags = ','
                    bp_fields = ' '
                    bp_tags = bp_tags + 'bp_port=' + iom_slot_id + '/' + \
                        bp_port_id + ',chassis=' + fex_id + \
                        ',fi_id=' + per_bp_port_dict['fi_id']
                    if 'peer_type' in per_bp_port_dict:
                        if per_bp_port_dict['peer_type'] != 'unknown':
                            if per_bp_port_dict['peer_type'] == 'S-chassis':
                                s_chassis = per_bp_port_dict['peer']
                                s_slot = ((per_bp_port_dict['peer_port']). \
                                            split('/'))[0]
                                s_blade = 'blade-' + s_slot
                                per_chassis_dict = chassis_dict[s_chassis]
                                blade_dict = per_chassis_dict['blades']
                                per_blade_dict = blade_dict[s_blade]
                                bp_tags = bp_tags + ',peer_service_profile=' + \
                                            per_blade_dict['service_profile']
                            else:
                                ru_dict = d_dict['ru']
                                per_ru_dict = \
                                    ru_dict[per_bp_port_dict['peer']]
                                bp_tags = bp_tags + ',peer_service_profile=' + \
                                            per_ru_dict['service_profile']
                    bp_tags, bp_fields = \
                                influxdb_lp_bp_ports(per_bp_port_dict, \
                                                     bp_tags, bp_fields)

                    final_print_string = final_print_string + bp_prefix \
                                            + bp_tags + bp_fields
            # Done: Build insert string for BackplanePortStats

    print(final_print_string)

def print_output():
    if user_args['verify_only']:
        logger.info('Skipping output in {} due to -V option' \
                    .format(user_args['output_format']))
    if user_args['output_format'] == 'dict':
        current_log_level = logger.level
        logger.setLevel(logging.DEBUG)
        logger.info('Printing output in dictionary format')
        logger.debug('stats_dict : \n {}'.format(json.dumps(stats_dict, indent=2)))
        logger.info('Printing output - DONE')
        logger.setLevel(current_log_level)
    if user_args['output_format'] == 'influxdb-lp':
        logger.info('Printing output in InfluxDB Line Protocol format')
        print_output_in_influxdb_lp()
        logger.info('Printing output - DONE')

###############################################################################
# END: Output functions
###############################################################################


# Key is the name of the stat, value is a list with first member as the NX-OS
# command and 2nd member as function to process the output (as dispatcher)
cli_stats_types = {
    'pfc_stats':['show interface priority-flow-control', parse_pfc_stats]
    }

def main(argv):
    # Initial tasks

    if not pre_checks_passed(argv):
        return
    parse_cmdline_arguments()
    setup_logging()
    start_time = time.time()
    logger.warning('---------- START (version {})----------'.format(__version__))
    get_ucs_domains()
    unpickle_connections()

    input_read_time = time.time()

    # Connect to UCS and pull stats. This section must be multi-threading aware
    try:
        get_ucs_stats()
    except Exception as e:
        logger.exception('Exception with get_ucs_stats')

    connect_time = time.time()

    # Parse the stats returned by UCS
    update_stats_dict()

    parse_time = time.time()

    # Print the stats as per the desired output format
    try:
        print_output()
    except Exception as e:
        logger.exception('Exception with print_output:{}'.format((str)(e)))

    output_time = time.time()

    # Final tasks
    pickle_connections()

    # Print response times per domain and total execution time
    time_output = ''
    if not user_args.get('verbose'):
        for domain_ip, time_d in response_time_dict.items():
            if time_d['cli_login'] > time_d['cli_start']:
                cli_login = (str)(round((time_d['cli_login'] - \
                                        time_d['cli_start']), 2))
            else:
                cli_login = 'N/A'

            if time_d['cli_end'] > time_d['cli_login']:
                cli_query = (str)(round((time_d['cli_end'] - \
                                        time_d['cli_login']), 2))
            else:
                cli_query = 'N/A'

            if time_d['sdk_login'] > time_d['sdk_start']:
                sdk_login = (str)(round((time_d['sdk_login'] - \
                                        time_d['sdk_start']), 2))
            else:
                sdk_login = 'N/A'

            if time_d['sdk_end'] > time_d['sdk_login']:
                sdk_query = (str)(round((time_d['sdk_end'] - \
                                        time_d['sdk_login']), 2))
            else:
                sdk_query = 'N/A'

            if user_args.get('no_ssh'):
                start_t = time_d['sdk_start']
                end_t = time_d['sdk_end']
            else:
                if time_d['cli_start'] < time_d['sdk_start']:
                    start_t = time_d['cli_start']
                else:
                    start_t = time_d['sdk_start']

                if time_d['cli_end'] > time_d['sdk_end']:
                    end_t = time_d['cli_end']
                else:
                    end_t = time_d['sdk_end']

            if end_t > start_t:
                total_t = (str)(round((end_t - start_t), 2))
            else:
                total_t = 'N/A'

            time_output = time_output + '\n'\
                        '    |--------------------------------------------|\n'\
                        '    |        Response time - {:<15}     | \n'\
                        '    |--------------------------------------------|\n'\
                        '    | CLI: Login:{:>8} s  | Query:{:>8} s  |\n'\
                        '    | SDK: Login:{:>8} s  | Query:{:>8} s  |\n'\
                        '    | Total: {:>8} s                          |\n'\
                        '    |--------------------------------------------|'.\
                        format(domain_ip, cli_login, cli_query, sdk_login,\
                               sdk_query, total_t)

    time_output = time_output + '\n' \
                   '    |--------------------------------------------|\n'\
                   '    |          Time taken to complete            |\n'\
                   '    |--------------------------------------------|\n'\
                   '    |                           Input:{:7.3f} s  |\n'\
                   '    | Connection setup and stats pull:{:7.3f} s  |\n'\
                   '    |                         Parsing:{:7.3f} s  |\n'\
                   '    |                          Output:{:7.3f} s  |\n'\
                   '    |------------------------------------------  |\n'\
                   '    |                           Total:{:7.3f} s  |\n'\
                   '    |--------------------------------------------|'.\
                   format((input_read_time - start_time),
                          (connect_time - input_read_time),
                          (parse_time - connect_time),
                          (output_time - parse_time),
                          (output_time - start_time))
    logger.setLevel(logging.INFO)
    logger.info('{}'.format(time_output))
    if (output_time - start_time) > (MASTER_TIMEOUT - 3):
        logger.warning('Total time taken to complete is high:{} s'. \
                        format(output_time - start_time))

    logger.warning('---------- END ----------')

if __name__ == '__main__':
    main(sys.argv)
