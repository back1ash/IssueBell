# IssueBell Kubernetes 배포

Argo CD는 `k8s/overlays/production` 경로를 감시합니다.

## 운영 Secret 생성

운영 Secret은 Git에 커밋하지 않습니다. `.env.production.example`을 복사해 실제 값을 입력한 뒤 다음을 실행합니다.

```bash
kubectl create namespace issuebell --dry-run=client -o yaml | kubectl apply -f -
kubectl -n issuebell create secret generic issuebell-secret \
  --from-env-file=.env.production \
  --dry-run=client -o yaml | kubectl apply -f -
```

## 배포 구조

- `base`: 모든 환경에서 공통으로 사용하는 앱, PostgreSQL, 서비스, PVC
- `overlays/production`: 운영 도메인, 환경 변수, 이미지 태그, Gateway API HTTPRoute

`issuebell.com` DNS가 OCI의 고정 공인 IP를 가리키고 Let’s Encrypt 인증서가 발급된 후 Argo CD 동기화를 활성화합니다.

## 최초 전환 순서

기존 서비스가 동작 중이라면 아래 순서를 지켜 한 번에 전환합니다.

1. PostgreSQL 비밀번호와 OAuth·Discord·애플리케이션 비밀값을 새로 발급해 `.env.production`에 입력합니다.
2. 위 명령으로 `issuebell-secret`을 클러스터에 생성합니다.
3. Cloudflare의 `issuebell.com` A 레코드를 OCI 공인 IP로 변경하고 DNS 전파를 확인합니다.
4. 인프라 저장소에서 `enable_issuebell_gateway: true`로 변경한 뒤 `gateway_api` 태그의 Ansible playbook을 실행합니다. TLS 인증서가 `Ready` 상태가 될 때까지 기다립니다.
5. 이 저장소의 변경 사항이 `main`에 반영된 뒤 `k8s/argocd-app.yaml`을 적용합니다.

```bash
kubectl apply -f k8s/argocd-app.yaml
kubectl -n argocd get application issuebell
```

Argo CD가 `k8s/overlays/production`을 동기화하고, 이후 `main` 브랜치에 애플리케이션 변경 사항을 push하면 GitHub Actions가 ARM64 이미지를 GHCR에 올린 뒤 immutable SHA 태그를 production overlay에 기록합니다.
