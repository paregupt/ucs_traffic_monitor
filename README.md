# UCS Traffic Monitoring (UTM)
Full-blown traffic monitoring of Cisco UCS blade servers using Grafana, InfluxDB and Telegraf.
![enter image description here](https://www.since2k7.com/wp-content/uploads/2020/02/g1-scaled.jpg)
![enter image description here](https://www.since2k7.com/wp-content/uploads/2020/02/g2-scaled.jpg)
![enter image description here](https://www.since2k7.com/wp-content/uploads/2020/02/g3-scaled.jpg)![enter image description here](https://www.since2k7.com/wp-content/uploads/2020/02/g5-scaled.jpg)![enter image description here](https://www.since2k7.com/wp-content/uploads/2020/02/g6-scaled.jpg)![enter image description here](https://www.since2k7.com/wp-content/uploads/2020/02/g4-scaled.jpg)

- **Data source**: [Cisco UCS Manager (UCSM)](https://www.cisco.com/c/en/us/products/servers-unified-computing/ucs-manager/index.html), read-only account is enough
- **Data receiver**: [Telegraf](https://github.com/influxdata/telegraf)
- **Data storage**: [InfluxDB](https://github.com/influxdata/influxdb), a time-series database
- **Visualization**: [Grafana](https://github.com/grafana/grafana)

## Installation
- Tested OS: CentOS 7.x. Should work on other OS also.
- Python version: Version 3 only. Should be able to work on Python 2 also with minor modification.

### Steps
1. Install Telegraf
2. Install InfluxDB
3. Install Grafana. Install following plugins:
    1. Flowchart
    2. Pie Chart
    3. ePict panel
4. Install apache webserver (Good to have, not mandatory)
4. Install following Python modules
    1. Cisco UCSM Python SDK
    2. netmiko library
    
## Configuration

[ucs_traffic_monitor.py](https://github.com/paregupt/ucs_traffic_monitor/blob/master/telegraf/ucs_traffic_monitor.py "ucs_traffic_monitor.py") fetches metrics from Cisco UCS and stitches them. This file is invoked by telegraf exec input plugin every 60 seconds. Login credentials of UCS should be available in [ucs_domains.txt](https://github.com/paregupt/ucs_traffic_monitor/blob/master/telegraf/ucs_domains.txt "ucs_domains.txt").

Try 
```shell
$ python3 /usr/local/telegraf/ucs_traffic_monitor.py -h
```
if you are running this for the first time.

Change/Add to your telegraf.conf file as below

```shell
[[inputs.exec]]
   interval = "60s"
   commands = [
       "python3 /usr/local/telegraf/ucs_traffic_monitor.py /usr/local/telegraf/ucs_domains.txt influxdb-lp -vvv",
   ]
   timeout = "50s"
   data_format = "influx"
```

also update the global values like

```shell
  logfile = "/var/log/telegraf/telegraf.log"
  logfile_rotation_max_size = "10MB"
  logfile_rotation_max_archives = 5
```
This should be able to 

 1. Pull metrics from UCS every 60 seconds
 2. Stitch them end-to-end between FI uplink ports and vNIC/vHBA on blade servers
 3. Write the data to InfluxDB

Import the [dashboards](https://github.com/paregupt/ucs_traffic_monitor/tree/master/grafana) into Grafana. You should have it running.

For detailed steps-by-step instructions, especially if you do not have prior experience with Grafana, InfluxDB and Telegraf, check out: [Cisco UCS monitoring using Grafana, InfluxDB, Telegraf – UTM Installation](https://www.since2k7.com/blog/2020/02/29/cisco-ucs-monitoring-using-grafana-influxdb-telegraf-utm-installation/)
 

