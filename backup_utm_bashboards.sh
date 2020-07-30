#!/bin/bash
# Initial Version coded on 26-Jul-2020 by Paresh (with Kiara)

UTM_DIR=/usr/local/telegraf

declare -A utm_dashboard_arr
utm_dashboard_arr=( ["locations"]="ri2OFp4Wz" ["domain_overview"]="Inte2EIWk" ["domain_traffic"]="W7LSukHWz" ["chassis_traffic"]="KOM8ZHNWz" ["service_profile"]="Z0M_N1vWz" ["ingress_congestion"]="Sve32sDZk" ["chassis_pause"]="SVO-VNiWk" ["local_sys"]="9CXO3jTWz")

date_suffix=$(date +"%M%H%m%d%y")

if ! command -v jq &> /dev/null
then
    echo "I need jq to proceed. Hint: yum install jq"
    exit
fi

testLoginValue="false"
while [[ "${testLoginValue}" == "false" ]]; do
    unset GRAFANA_USER
    unset GRAFANA_PASSWORD
    while [ -z "$GRAFANA_USER" ]; do
        read -p "Grafana User:" GRAFANA_USER
    done
    while [ -z "$GRAFANA_PASSWORD" ]; do
        read -s -p "Password:" GRAFANA_PASSWORD
    done
    echo " "

    auth_msg=$(curl -s -X GET -H "Accept: application/json" -H "Content-Type: application/json" http://$GRAFANA_USER:$GRAFANA_PASSWORD@localhost:3000/api/org | jq '.message')
    if [[ "$auth_msg" == *"Invalid"* ]]; then
        echo "$auth_msg"
        echo "Please try again"
    else
        testLoginValue="true"
        echo ""
    fi
done

echo "---------------------------"
echo "Taking backup of UTM dashboards and cleanup for sharing"
sleep 2

if mkdir -p $UTM_DIR/grafana/dashboards_${date_suffix} ; then
    echo "."

    for item in "${!utm_dashboard_arr[@]}"
    do
        curl -s -X GET -H "Accept: application/json" -H "Content-Type: application/json" http://$GRAFANA_USER:$GRAFANA_PASSWORD@localhost:3000/api/dashboards/uid/${utm_dashboard_arr[$item]} | jq -r '.dashboard' > $UTM_DIR/grafana/dashboards_${date_suffix}/${item}.json
        fid_arr+=( ["${item}"]="$(jq -r '.meta.folderId' $UTM_DIR/grafana/dashboards_${date_suffix}/${item}.json)" )
    done

    #for key in ${!uid_arr[@]}; do
    #    echo ${key} ${uid_arr[${key}]}
    #done

    echo "I have taken a backup of your existing UTM dashboards in $UTM_DIR/grafana/dashboards_${date_suffix}"
else
    echo "Unable to create $UTM_DIR/grafana/dashboards_${date_suffix}"
fi

echo "---------------------------"
sleep 2
