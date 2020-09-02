#!/bin/bash

# Start vagrant VM
vagrant up

# Install community.grafana
ansible-galaxy collection install community.grafana

# Test ansible ssh connection
ansible -i inventory all -m shell -a "uptime"

# Perform installation (must be run from ansible-install folder)
ansible-playbook -i inventory prereq.yml
ansible-playbook -i inventory utm.yml
