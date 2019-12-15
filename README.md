# UCS Traffic Monitoring (UTM)

This repository maintains config files of a full-blown traffic monitoring app of Cisco UCS servers. 
- **Data source**: [Cisco UCS Manager (UCSM)](https://www.cisco.com/c/en/us/products/servers-unified-computing/ucs-manager/index.html), read-only account is required
- **Data receiver**: [Telegraf](https://github.com/influxdata/telegraf)
- **Data storage**: [InfluxDB](https://github.com/influxdata/influxdb), a time-series database
- **Visualization**: [Grafana](https://github.com/grafana/grafana)

## Installation
- Tested OS: CentOS 7.x. Should work on other OS also.
- Python version: Version 3 only. Should be able to work on Python 2 also with minor modification.
- 

### Steps
1. Install Telegraf
2. Install InfluxDB
3. Install Grafana. Install following plugins:
    1. Flowchart
    2. Pie Chart
    3. ePict panel
4. Install apache webserver
4. Install following Python modules
    1. Cisco UCSM Python SDK
    2. netmiko library
    
## Initial configuration


a File to 
Cisco UCS traffic monitoring using Grafana, InfluxDB and Telegraf
