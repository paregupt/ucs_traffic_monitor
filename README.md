# UCS Traffic Monitoring (UTM)
Full-blown traffic monitoring of Cisco UCS servers using Grafana, InfluxDB and Telegraf.

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

Two options:
- DIY Installation: Self install the required pacakges
- OVA - Required packages are pre-installed on CentOS 7.6 OVA

### DIY Installation
1. Install Telegraf
2. Install InfluxDB
3. Install Grafana. Install following plugins:
    1. Flowchart
    2. Pie Chart
    3. ePict panel
    4. multistat
4. Install apache webserver (Good to have, not mandatory)
4. Install following Python modules
    1. Cisco UCSM Python SDK
    2. netmiko library
    
### OVA installation
[Download OVA from releases page](https://github.com/paregupt/ucs_traffic_monitor/releases).
This is a CentOS 7.6 based OVA. Deployment is same as any other OVA that you have deployed before.
Please upgrade to the latest after deploying the OVA. 

## Upgrades
Replace the existing ucs_traffic_monitor.py file with the later version. You can also upgrade the Grafana dashboards by copy-pasting or importing the JSON.

You are responsible to upgrade Grafana, InfluxDB, Telegraf, Python and other packages. Generally, the upgrade is simple with one or two commands. Please refer to respective packages for upgrade process. Please keep an qye on security vulnerabilities and fixes. Grafana, InfluxDB, etc. are prompt in fixing the CVEs. You may want to run the latest versions to have all the fixes. This may occasionally break a few use-cases. But few broken features are better than security holes.

## Configuration

[ucs_traffic_monitor.py](https://github.com/paregupt/ucs_traffic_monitor/blob/master/telegraf/ucs_traffic_monitor.py "ucs_traffic_monitor.py") fetches metrics from Cisco UCS and stitches them. This file is invoked by telegraf exec input plugin every 60 seconds. Login credentials of UCS should be available in ucs_domains_group*.txt.

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
       "python3 /usr/local/telegraf/ucs_traffic_monitor.py /usr/local/telegraf/ucs_domains.txt influxdb-lp -vv",
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

Import the [dashboards](https://github.com/paregupt/ucs_traffic_monitor/tree/master/grafana/dashboards) into Grafana. You should have it running.

For detailed steps-by-step instructions, especially if you do not have prior experience with Grafana, InfluxDB and Telegraf, check out: [Cisco UCS monitoring using Grafana, InfluxDB, Telegraf – UTM Installation](https://www.since2k7.com/blog/2020/02/29/cisco-ucs-monitoring-using-grafana-influxdb-telegraf-utm-installation/)

## Credits
- My wife (Dimple) and kids (Manan and Kiara) while I took away precious weekend hours from you and invested in the development of UTM.
- Folks in the Cisco UCS business unit and TAC, who knowingly or unknowingly helped me to build UTM and also for awesome content on ciscolive.com.
- Colleagues and friends in Cisco (Art, Craig, Eugene, Mark and a long list of people) for the inspiration. 
- End-users/customers: Philipe, Jason, Shawn, Ryan and others for your great feedback.
