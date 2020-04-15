
As of UTM 0.3, it is known to work with following environments.

From my lab (9 UCS domains, 12 chassis, 74 servers):

    FI model         UCSM version FI firmware version
    --------         ------------ -------------------
    UCS-FI-6248UP    4.0(1c)      5.0(3)N2(4.01c)
    UCS-FI-6332-16UP 4.0(4b)      5.0(3)N2(4.04a)
    UCS-FI-6248UP    3.2(3c)      5.0(3)N2(3.23b)
    UCS-FI-6248UP    2.2(6e)      5.2(3)N2(2.26e)
    UCS-FI-64108     4.1(1a)      7.0(3)N2(4.11a)
    UCS-FI-6332-16UP 4.0(4d)      5.0(3)N2(4.04b)
    UCS-FI-6454      4.1(1b)      7.0(3)N2(4.11aS3)
    UCS-FI-6332-16UP 4.0(4g)      5.0(3)N2(4.04e)
    UCS-FI-6248UP    4.0(4f)      5.0(3)N2(4.04d)

-------------------------------------------------

From a customer (23 UCS domains, 196 chassis, 1610 servers):

    FI model         UCSM version FI firmware version 
    --------         ------------ ------------------- 
    UCS-FI-6332-16UP 4.1(1a)      5.0(3)N2(4.11a) 
    UCS-FI-6332-16UP 4.1(1a)      5.0(3)N2(4.11a) 
    UCS-FI-6296UP    4.0(4c)      5.0(3)N2(4.04b) 
    UCS-FI-6296UP    4.0(4c)      5.0(3)N2(4.04b) 
    UCS-FI-6296UP    4.0(4c)      5.0(3)N2(4.04b) 
    UCS-FI-6296UP    4.0(4c)      5.0(3)N2(4.04b) 
    UCS-FI-6332-16UP 4.0(4c)      5.0(3)N2(4.04b) 
    UCS-FI-6332-16UP 4.0(4c)      5.0(3)N2(4.04b) 
    UCS-FI-6296UP    4.0(4c)      5.0(3)N2(4.04b) 
    UCS-FI-6296UP    4.0(4c)      5.0(3)N2(4.04b) 
    UCS-FI-6332-16UP 4.0(4c)      5.0(3)N2(4.04b) 
    UCS-FI-6332-16UP 4.0(4c)      5.0(3)N2(4.04b) 
    UCS-FI-6332-16UP 4.0(4c)      5.0(3)N2(4.04b) 
    UCS-FI-6296UP    4.0(4c)      5.0(3)N2(4.04b) 
    UCS-FI-6296UP    4.0(4c)      5.0(3)N2(4.04b) 
    UCS-FI-6296UP    4.0(4c)      5.0(3)N2(4.04b) 
    UCS-FI-6296UP    4.0(4c)      5.0(3)N2(4.04b) 
    UCS-FI-6296UP    4.0(4c)      5.0(3)N2(4.04b) 
    UCS-FI-6332-16UP 4.0(4c)      5.0(3)N2(4.04b) 
    UCS-FI-6296UP    4.0(4c)      5.0(3)N2(4.04b) 
    UCS-FI-6296UP    4.0(4c)      5.0(3)N2(4.04b) 
    UCS-FI-6296UP    4.0(4c)      5.0(3)N2(4.04b) 
    UCS-FI-6332-16UP 4.0(4c)      5.0(3)N2(4.04b) 

-------------------------------------------------

If you are running UTM, please help the community by sharing this information at https://github.com/paregupt/ucs_traffic_monitor/issues/11. Your peers in the industry to feel confident before deploying UTM. 

You can run following command from bash shell to get above information from your installation

    $ influx -format=column -database 'telegraf' -execute 'select last as "FI model", ucsm_fw_ver as "UCSM version", fi_fw_sys_ver as "FI firmware version" from (select last(model),ucsm_fw_ver,fi_fw_sys_ver from FIEnvStats group by domain)' | sed 1d | sed 's/^....................//'
    $ influx -format=column -database 'telegraf' -execute 'select count(id) as "Number of servers" from (SELECT last("model") FROM "autogen"."Servers" GROUP BY domain,chassis,blade,id)' | sed 1d | sed 's/^.....//'
    $ influx -format=column -database 'telegraf' -execute 'select count(domain) as "Number of UCS domains" from (SELECT last("model") FROM "autogen"."FIEnvStats" GROUP BY domain)'  | sed 1d | sed 's/^.....//'
