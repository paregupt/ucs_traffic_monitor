#!/bin/bash
# Initial Version coded on 26-Jul-2020 by Paresh (with Kiara)
# Run this file from the 

UTM_VERSION=5
UTM_DIR=/usr/local/telegraf
GRAFANA_IMG_DIR=/usr/share/grafana/public/img
GRAFANA_PLUGIN_DIR=/var/lib/grafana/plugins

# Grafana uid is globally unique. This is used for sharing and maintaining links between dashboards
# Dashboard id is unique per installation
# Folder id is used as per existing folder

declare -A utm_dashboard_arr
utm_dashboard_arr=( ["locations"]="ri2OFp4Wz" ["domain_overview"]="Inte2EIWk" ["domain_traffic"]="W7LSukHWz" ["chassis_traffic"]="KOM8ZHNWz" ["service_profile"]="Z0M_N1vWz" ["ingress_congestion"]="Sve32sDZk" ["chassis_pause"]="SVO-VNiWk" ["local_sys"]="9CXO3jTWz")

declare -A fid_arr

declare -A db_id_arr

date_suffix=$(date +"%M%H%m%d%y")

KIARA_BIRTH=1528354800
TODAY=$(date +%s)
DIFF=$(( $TODAY - $KIARA_BIRTH ))
KIARA_AGE=$(( $DIFF/( 60 * 60 * 24 * 365) ))

echo ""
echo "---------------------------"
echo "Hi there. I am Kiara."
echo "I am $KIARA_AGE years old and I can help you in upgrading your UTM installation."
echo "I learned it with my daddy while he was working on it."
echo "---------------------------"
echo ""
echo "---------------------------"
read -p "May I continue? (y/n):" -n 1 -r
echo    # (optional) move to a new line
if [[ $REPLY =~ ^[Nn]$ ]]
then
    echo "---------------------------"
    echo "Bye - Kiara."
    echo ""
    exit
fi

if ! command -v jq &> /dev/null
then
    echo "I need jq to proceed. Hint: yum install jq"
    echo "---------------------------"
    echo "Bye - Kiara."
    echo ""
    exit
fi


echo "---------------------------"
echo "I can upgrade the complete UTM app or just the UI dashboards."
read -p "What do you want me to do? (a(ll) for total upgrade, any other key to upgrade UI dashboards only):" -n 1 -r
echo    # (optional) move to a new line
if [[ $REPLY =~ ^[Aa]$ ]] ; then
    echo "---------------------------"
    echo "Alright! Let's get started. Upgrading UTM will take just a few minutes."
    echo "First, I need some new packages. I can download them if this machine can access the Internet"
    echo ""
    echo "If this machine can't reach the Internet, please:"
    echo "  1. Download and install https://dl.grafana.com/oss/release/grafana-7.5.7-1.x86_64.rpm"
    echo "      1.1. Earlier versions of Grafana will work with reduced functionality"
    echo "  2. Download and install https://grafana.com/api/plugins/agenty-flowcharting-panel/versions/0.9.0/download"
    echo "      2.1. Minimum required version 0.9"
    echo "  3. Download and install https://grafana.com/api/plugins/michaeldmoore-multistat-panel/versions/1.7.1/download"
    echo "      3.1. Minimum required version 1.4.1"
    echo "  4. Restart Grafana: systemctl restart grafana-server"
    echo "  5. Download and install wget https://dl.influxdata.com/telegraf/releases/telegraf-1.18.3-1.x86_64.rpm"
    echo "  6. Restart Telegraf: systemctl restart telegraf"
    echo "---------------------------"
    
    echo ""
    read -p "May I access the Internet now? (y to keep me going, n to install them manually):" -n 1 -r
    echo    # (optional) move to a new line
    if [[ $REPLY =~ ^[Yy]$ ]]
    then
        echo "---------------------------"
        echo "Downloading and upgrading Grafana ..."
        if wget https://dl.grafana.com/oss/release/grafana-7.5.7-1.x86_64.rpm ; then
            if yum -y install grafana-7.5.7-1.x86_64.rpm ; then
                echo "Grafana upgrade done"
            else
                echo "I could not upgrade Grafana"
            fi
            echo ""
        else
            echo "---------------------------"
            echo "I could not make that work"
        fi
        echo "---------------------------"
        echo "Downloading and upgrading Telegraf ..."
        if wget https://dl.influxdata.com/telegraf/releases/telegraf-1.18.3-1.x86_64.rpm ; then
            if yum -y localinstall telegraf-1.18.3-1.x86_64.rpm ; then
                echo "Telegraf upgrade done"
                systemctl restart telegraf
            else
                echo "I could not upgrade Telegraf"
            fi
            echo ""
        else
            echo "---------------------------"
            echo "I could not make that work"
        fi

        echo "---------------------------"
        echo "Downloading and upgrading other packages ..."
        echo "---------------------------"
        #if git clone https://github.com/algenty/grafana-flowcharting.git ; then
        if grafana-cli plugins install agenty-flowcharting-panel ; then
            echo "flowcharting upgrade done"
        else
            echo "---------------------------"
            echo "I could not make that work"
        fi
        echo "---------------------------"
        #if git clone https://github.com/michaeldmoore/michaeldmoore-multistat-panel.git ; then
        if grafana-cli plugins install michaeldmoore-multistat-panel ; then
            echo "multistat upgrade done"
        else
            echo "---------------------------"
            echo "I could not make that work"
        fi
    fi
    
    echo "---------------------------"
    echo ""
    sleep 2
    
    echo "---------------------------"
    echo "Upgrading UTM images ..."
    sleep 2
    mkdir -p $GRAFANA_IMG_DIR/utm
    if cp images/* $GRAFANA_IMG_DIR/utm/ ; then
        echo "Images upgrade done"
    else
        echo "I could not copy UTM images. You can move UTM images to $GRAFANA_IMG_DIR/utm/ later"
    fi
    echo "---------------------------"
    
    echo " "
    sleep 2
    
    echo "---------------------------"
    echo "Upgrading UTM receiver ..."
    sleep 2
    utm_old_ver=$(grep -Po '(?<=__version__ =).*' $UTM_DIR/ucs_traffic_monitor.py | awk -F'"' '{print $2}')
    utm_new_ver=$(grep -Po '(?<=__version__ =).*' telegraf/ucs_traffic_monitor.py | awk -F'"' '{print $2}')
    echo "Your existing UTM version is $utm_old_ver"
    echo "First, let me take a backup ..."
    sleep 2
    if cp $UTM_DIR/ucs_traffic_monitor.py $UTM_DIR/ucs_traffic_monitor_$utm_old_ver.py ; then
        echo "Done: $UTM_DIR/ucs_traffic_monitor_$utm_old_ver.py"
    else
        echo "I could not take a backup of your existing UTM receiver. Still trying to continue..."
    fi
    echo "Upgrading UTM receiver ..."
    sleep 2
    if cp telegraf/ucs_traffic_monitor.py $UTM_DIR/ucs_traffic_monitor.py ; then
        echo "Your UTM receiver is now upgraded to version $utm_new_ver"
    else
        echo "---------------------------"
        echo "I could not upgrade the UTM receiver."
    fi
    
    echo "---------------------------"
    echo " "
    sleep 2

    echo "---------------------------"
    echo "In the earlier releases of UTM, disable_sanitize_html flag was enabled. It is not required anymore."
    echo "I am turning this off."
    sleep 2
    sed -i '/^disable_sanitize_html*/s/^/;/' /etc/grafana/grafana.ini
    echo "Done"
    echo "---------------------------"
    echo " "
    sleep 2

fi

read -p "Are you ready to upgrade UI dashboards: (y/n)" -n 1 -r
echo    # (optional) move to a new line
if [[ $REPLY =~ ^[Yy]$ ]] ; then
    echo "---------------------------"
    echo "I need Grafana Credentials."
    echo "Hint:admin/Utm_12345"
    echo "---------------------------"
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
    echo "First, let's backup your existing UTM dashboards"
    sleep 2
    
    mkdir -p $UTM_DIR/grafana/dashboards_${date_suffix}
    
    for item in "${!utm_dashboard_arr[@]}" ; do
        curl -s -X GET -H "Accept: application/json" -H "Content-Type: application/json" http://$GRAFANA_USER:$GRAFANA_PASSWORD@localhost:3000/api/dashboards/uid/${utm_dashboard_arr[$item]} > $UTM_DIR/grafana/dashboards_${date_suffix}/${item}.json
        fid_arr+=( ["${item}"]="$(jq -r '.meta.folderId' $UTM_DIR/grafana/dashboards_${date_suffix}/${item}.json)" )
        db_id_arr+=( ["${item}"]="$(jq -r '.dashboard.id' $UTM_DIR/grafana/dashboards_${date_suffix}/${item}.json)" )
    done
    
    #for key in ${!fid_arr[@]}; do
    #    echo ${key} ${fid_arr[${key}]}
    #done
    
    echo "I have taken a backup of your existing UTM dashboards in $UTM_DIR/grafana/dashboards_${date_suffix}"
    echo "---------------------------"
    sleep 2
    
    #Add at the beginning of file: sed -i '1 i\{ "dashboard":' 1.json
    #Add at the end of the file: sed -i -e '$a,\n"folderId":1,\n"overwrite":true\n}'
    #Delete a key: jq 'del(.meta)' json
    #Update key value jq '.dashboard.version = 4' json
    #Add keys jq '. + { "folderId": 1, "overwrite":true }'
    # All together jq 'del(.meta)' input.json | jq '.dashboard.version = 4' | jq '. + { "folderId": 1, "overwrite":true }' > 1.json
    echo "Now, I will try to upgrade UTM dashboards. "
    read -p "Are you sure: (y/n)" -n 1 -r
    echo    # (optional) move to a new line
    if [[ $REPLY =~ ^[Yy]$ ]] ; then
        locations_uid=${utm_dashboard_arr["locations"]}
        locations_id=1
        #for i in $(ls grafana/dashboards/*.json)
        for item in "${!utm_dashboard_arr[@]}" ; do
            cp grafana/dashboards/${item}.json /tmp/${item}.json
            # Use the same folderId and id from the existing dashboards
            fid=${fid_arr[${item}]}
            db_id=${db_id_arr[${item}]}
            printf 'Upgrading %-25s -- in Folder %-3s -- with ID %-3s -- to UTM version %-3s\n' "$item" "$fid" "$db_id" "$UTM_VERSION"
            # update UTM version
            jq '{"dashboard": .}' /tmp/${item}.json > /tmp/${item}_1.json
            jq ".dashboard.version = $UTM_VERSION" /tmp/${item}_1.json > /tmp/${item}.json
            # use the existing dashboard id
            jq ".dashboard.id = $db_id" /tmp/${item}.json > /tmp/${item}_1.json
            # add folderId and overwrite flag
            jq ". + { "folderId": $fid, "overwrite":true }" /tmp/${item}_1.json > /tmp/${item}.json
            location_db_id=$(curl -s -X POST -H "Accept: application/json" -H "Content-Type: application/json" -d @/tmp/${item}.json http://$GRAFANA_USER:$GRAFANA_PASSWORD@localhost:3000/api/dashboards/db | jq -r '. | select(.. | .uid? == '\"$locations_uid\"').id')
            if [ -z "$location_db_id" ] ; then
                echo "."
            else
                echo "."
                locations_id=$location_db_id
            fi
        done
        echo "Done"
    
        echo ""
        echo "UTM dashboards upgraded"
        echo "---------------------------"
        sleep 2
        echo "Update home dashboard to ID $locations_id ..."
        curl -s -X PUT -H "Accept: application/json" -H "Content-Type: application/json" -d "{\"theme\":\"\",\"homeDashboardId\":$locations_id,\"timezone\":\"\"}" http://$GRAFANA_USER:$GRAFANA_PASSWORD@localhost:3000/api/user/preferences
    
        echo ""
        sleep 2
        echo "That is all."
    fi
fi

echo "---------------------------"
echo "..."
systemctl restart grafana-server
echo "---------------------------"

echo "Bye - Kiara."
echo "---------------------------"
sleep 1
