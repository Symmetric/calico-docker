## Running `calico-node`
### Install calicoctl
The v0.7.0 release of `calicoctl` will allow you to start the `calico-node` docker image and install our v0.2.0 Calico Network Plugin.

```
wget https://github.com/projectcalico/calico-docker/releases/download/v0.7.0/calicoctl
chmod +x calicoctl
sudo mv calicoctl /usr/bin/
```


```
sudo modprobe ipip; sudo modprobe xt_set
sudo ETCD_AUTHORITY=<MASTER_PRIVATE_IPV4>:6666 calicoctl node --kubernetes
```

### Configure the Kubelet
To start using the Calico Network Plugin, we will need to modify the existing kubelet process on each of your nodes. First, you will need to create a `network-environment` file with the following contents: 
```
DEFAULT_IPV4=<IP>
KUBERNETES_MASTER=<MASTER_PRIVATE_IPV4>:8080
ETCD_AUTHORITY=<MASTER_PRIVATE_IPV4>:6666
KUBE_API_ROOT=http://<MASTER_PRIVATE_IPV4>:8080/api/v1/
CALICO_IPAM=true
```

In your kubelet service config files, append the `--network_plugin=calico` flag to the `ExecStart` command and add the following line
```
EnvironmentFile=/path/to/network-environment
```

Then, restart the kubelet.
```
sudo systemctl daemon-reload
sudo systemctl restart kubelet
```