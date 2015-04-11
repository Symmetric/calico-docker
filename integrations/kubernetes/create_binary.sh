#!/bin/bash

set -e

docker build -t calico-kubernetes-build .

docker run -u user -v `pwd`/:/code/calico -v `pwd`/dist:/code/dist calico-build pyinstaller calico/calico.py -a -F -s --clean
#docker run -u user -v `pwd`/dist:/code/dist calico-build bash -c 'docopt-completion --manual-bash dist/calico && mv calicoctl.sh dist'

echo "Build output is in dist/"
echo "Copy dist/calicoctl.sh to /etc/bash_completion.d/ to get bash completion"
