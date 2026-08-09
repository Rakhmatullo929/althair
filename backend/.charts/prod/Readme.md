# How to use

1. Add repo
```shell
helm repo add helm-templates https://gitlab.com/cranky4.89/helm-templates --username=user --password=token
```
1. Update repo
```shell
helm repo update
```
1. Install the app
```shell
helm install --atomic --timeout=60s --create-namespace -n mmc8534412 --version 0.2.73 -f ./values.yaml -f ./values-local.yaml backend helm-templates/service
```
1. Update the app
```shell
helm upgrade --atomic --timeout=60s -n mmc8534412 --version 0.2.73 --reuse-values -f values.yaml -f values-local.yaml --set appVersion=dev backend helm-templates/service
```


### Uninstall
```shell
helm uninstall -n mmc8534412 backend

```