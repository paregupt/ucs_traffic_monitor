#!/bin/bash


# Get UTM from github
git clone https://github.com/paregupt/ucs_traffic_monitor


# Prepare updated version of grafana dashboard if using vagrant (port 80 is not available, will use 8080 instead)
rm -Rf /tmp/utm
mkdir -p /tmp/utm
cp ucs_traffic_monitor/grafana/dashboards/* /tmp/utm
sed -i 's;window.location.hostname + \\"/cisco_logo.png;window.location.hostname + \\":8080/cisco_logo.png;g' /tmp/utm/*  
sed -i 's;window.location.hostname + \\"/world_map.png;window.location.hostname + \\":8080/world_map.png;g'   /tmp/utm/*  
sed -i 's;window.location.hostname + \\"/your_logo.png;window.location.hostname + \\":8080/your_logo.png;g'   /tmp/utm/*  


# Start vagrant VM and configure
vagrant up
ansible-playbook -i utm.vagrant prereq.yml
ansible-playbook -i utm.vagrant utm.yml --extra-vars "alternate_grafana_dashboard=/tmp/utm"
