# UCS Traffic Monitoring (UTM)
Full-blown traffic monitoring of Cisco UCS servers using Grafana, InfluxDB and Telegraf.

PS: Please help decide UTM enhancements by these polls: https://github.com/paregupt/ucs_traffic_monitor/discussions

# Sister Projects
## Looking for something similar to monitor Cisco MDS Switches?
[Click here to check out MDS Traffic Monitoring (MTM)](https://github.com/paregupt/mds_traffic_monitor)

## Looking for something similar to monitor Cisco Nexus Switches?
[Click here to check out Nexus Traffic Monitoring (NTM)](https://github.com/paregupt/nexus_traffic_monitor)

# Use cases
Locations Dashboard
![enter image description here](https://www.since2k7.com/wp-content/uploads/2020/07/utm_0.4-1.png)

UCS Domains Overview
![enter image description here](https://www.since2k7.com/wp-content/uploads/2020/07/utm_0.4-3.png)

Top 10 ports, service profiles, etc.
![UTM_v0 6-overview](https://user-images.githubusercontent.com/8773072/122630893-12828d80-d07c-11eb-838c-c298e322e38d.gif)

Load Balance verification and root cause
![enter image description here](https://www.since2k7.com/wp-content/uploads/2020/07/utm_0.4-4.png)

Congestion Monitoring and detection 
![UTM_v0 6-congestion](https://user-images.githubusercontent.com/8773072/122630885-0696cb80-d07c-11eb-852b-dbc5f1722606.jpg)

End-to-end mapping from vHBA/vNIC to FI uplink Port
![enter image description here](https://www.since2k7.com/wp-content/uploads/2020/07/utm_0.4-8.png)

Integrated documentation with conceptual drawing and detailed explanations
![enter image description here](https://www.since2k7.com/wp-content/uploads/2020/07/utm_0.4-10.png)

Link utilization and errors
![UTM_v0 6-link-tabular-view](https://user-images.githubusercontent.com/8773072/122631083-b28ce680-d07d-11eb-8cc0-05d260fe6147.jpg)

and much more...

- **Data source**: [Cisco UCS Manager (UCSM)](https://www.cisco.com/c/en/us/products/servers-unified-computing/ucs-manager/index.html), read-only account is enough
- **Data receiver**: [Telegraf](https://github.com/influxdata/telegraf)
- **Data storage**: [InfluxDB](https://github.com/influxdata/influxdb), a time-series database
- **Visualization**: [Grafana](https://github.com/grafana/grafana)

# Installation
- Tested OS: CentOS 7.x. Should work on other OS also.
- Python version: Version 3 only. Should be able to work on Python 2 also with minor modification.

Two options:
- DIY Installation: Self install the required packages (or take a look to [ansible-install](ansible-install) folder where you could let the machine work for you)
- OVA - Required packages are pre-installed on CentOS 7.6 OVA

## DIY Installation
1. Install Telegraf
1. Install InfluxDB
1. Install Grafana. Install following plugins:
    1. Flowchart
    1. Pie Chart (using Pie chart v2 starting UTM v0.6)
    1. ePict panel (Not needed starting UTM v0.6)
    1. multistat (Not needed starting UTM v0.6)
1. Install following Python modules
    1. Cisco UCSM Python SDK
    1. netmiko library
    
## OVA installation
[Download OVA from releases page](https://github.com/paregupt/ucs_traffic_monitor/releases).
This is a CentOS 7.6 based OVA. Deployment is same as any other OVA that you have deployed before. [Click here for detailed installation instructions of the UTM OVA](https://www.since2k7.com/blog/2020/02/29/cisco-ucs-monitoring-using-grafana-influxdb-telegraf-utm-installation/#Installing_UTM_using_OVA). The OVA is based on v0.3. Upgrading to the latest must be your first step.

## Upgrades
You are responsible to upgrade Grafana, InfluxDB, Telegraf, Python and other packages. Upgrading UTM is simple with one or two commands and doesn't take more than a few minutes. Please refer to respective packages for upgrade process. Please keep a watch on the security vulnerabilities and fixes.

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

Import the [dashboards](https://github.com/paregupt/ucs_traffic_monitor/tree/master/grafana/dashboards) into Grafana. That's all. UTM should be fully functional.

For detailed steps-by-step instructions, especially if you do not have prior experience with Grafana, InfluxDB and Telegraf, check out: [Cisco UCS monitoring using Grafana, InfluxDB, Telegraf – UTM Installation](https://www.since2k7.com/blog/2020/02/29/cisco-ucs-monitoring-using-grafana-influxdb-telegraf-utm-installation/)


# Credits
- My wife (Dimple) and kids (Manan and Kiara) while I took away precious weekend hours from you and invested in the development of UTM.
- Folks in the Cisco UCS business unit and TAC, who knowingly or unknowingly helped me to build UTM and also for awesome content on ciscolive.com.
- Colleagues and friends in Cisco (Art, Craig, Eugene, Mark and a long list of people) for the inspiration. 
- End-users/customers: Philipe, Jason, Shawn, Ryan, Ian, and others for your great feedback.
