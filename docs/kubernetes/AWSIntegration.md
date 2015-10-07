# Integration with an AWS Kubernetes cluster
This guide will walk you through how to use Calico Networking with an existing AWS-Kubernetes cluster.

## Requirements
* A working Kubernetes Deployment on AWS
    - While any AWS deployment will do, we recommend the Kubernetes [`kube-up` guide](https://github.com/kubernetes/kubernetes/blob/release-1.0/docs/getting-started-guides/aws.md) for AWS. This guide was created with the `kube-up` script in mind.
* SSH access to your master and minions
    - Unless otherwise specified, the `kube-up` script will create a `kube_aws_rsa` private key in the `~/.ssh` folder which you can use to access your AWS Instances.
    - SSH in with the following command `ssh -i </path/to/key> ubuntu@<PUBLIC_IP>`

## Preparing your master services
### Setting up Calico's etcd backend
On your master, download our [etcd manifest](https://raw.githubusercontent.com/projectcalico/calico-kubernetes-coreos-demo/ah3-config-update/manifests/calico-etcd.manifest) and replace all instances of `<PRIVATE_IPV4>` with your master's IP or hostname. Then, install the manifest in the `/etc/kubernetes/manifests/` directory. The kubelet on your master should automatically spin up a docker container for the new etcd which can be accessed on port 6666 of your master.

### Reconfiguring the Kubernetes API and services
For now, our plugin does not support token authentication for API access. While we implement more advanced security features, you will have to configure your `kube-apiserver` on the master to use an `insecure-bind-address` and an `insecure-port`. 
* The `kube-up` script will implement a secure apiserver by default. To change this you will need to access the `/etc/kubernetes/manifests/kube-apiserver.manifest` file. In the `ExecStart` section, add `--insecure-bind-address=<PRIVATE_IPV4>` and `--insecure-port=8080`.
* You may also have to reconfigure the `kube-controller-manager` and `kube-scheduler` manifests to point to the insecure private IP instead of the secure `127.0.0.1` loopback address.

## Running `calico-node`
On each of your AWS Instances, perform the following steps.

### Install calicoctl
Download and install the `calicoctl` binary
```
wget https://github.com/projectcalico/calico-docker/releases/download/v0.7.0/calicoctl
chmod +x calicoctl
sudo mv calicoctl /usr/bin/
```

Running `calicoctl node` will pull the `calico-node` Docker Image and the `calico-kubernetes` plugin binary.
```
sudo ETCD_AUTHORITY=<MASTER_PRIVATE_IPV4>:6666 calicoctl node --kubernetes
```

If you plan on using ipip features, set up a pool with ipip enabled
> Note: You only need to call `pool add` once per cluster.

```
sudo modprobe ipip
sudo calicoctl pool add 192.168.0.0/16 --ipip
```

### Configure the Kubelet
To start using the Calico Network Plugin, we will need to modify the existing kubelet process on each of your nodes. First, you will need to create a `network-environment` file with the following contents: 
```
KUBERNETES_MASTER=<MASTER_PRIVATE_IPV4>:8080
ETCD_AUTHORITY=<MASTER_PRIVATE_IPV4>:6666
KUBE_API_ROOT=http://<MASTER_PRIVATE_IPV4>:8080/api/v1/
CALICO_IPAM=true
```

In your kubelet service config files, append the `--network_plugin=calico` flag to the `ExecStart` command and add the following line.
```
EnvironmentFile=/path/to/network-environment
```

Then, restart the kubelet.
```
sudo systemctl daemon-reload
sudo systemctl restart kubelet
```

Now you are ready to begin using Calico Networking!

For more information on configuring Calico for Kubernetes, see our [Kubernetes Integration docs](KubernetesIntegration.md).

For more information on programming Calico Policy in Kubernetes, see our [Kubernetes Policy docs](KubernetesPolicy.md).
