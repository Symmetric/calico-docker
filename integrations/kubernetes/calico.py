#!/bin/python
import sys
import re
from subprocess import check_output
from calicoctl import container_add

if __name__ == '__main__':
    print('Args: %s' % sys.argv)
    workload_docker_id = sys.argv[4]
    ip_addr_output = check_output(['docker', 'exec', workload_docker_id, 'ifconfig', 'eth0'])
    ip = re.match('inet addr:((?:\d+\.){3}\d+')
    print('IP=%s', ip)
