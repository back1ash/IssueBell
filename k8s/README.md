# IssueBell Kubernetes 배포

Argo CD는 `k8s/overlays/production` 경로를 감시합니다.

## PostgreSQL Object Storage 백업

`k8s/base/postgres-backup-cronjob.yaml`은 매일 03:00 UTC(한국 시간 12:00)에 PostgreSQL 16의 `issuebell` 데이터베이스를 custom-format 덤프로 만들고, OCI Object Storage의 비공개 `issuebell-db-backups` 버킷에 업로드한 뒤 원격 객체를 확인합니다. 덤프 파일은 Job Pod의 임시 볼륨에만 만들어집니다.

### OCI 버킷 및 권한

`terraform/objectstorage-backups`의 독립 Terraform root가 버킷과 권한을 관리합니다. 인프라 Terraform과 state가 분리되어 있어 여기서 apply해도 VM·네트워크 리소스에는 변경이 없습니다.

- 이름: `issuebell-db-backups`
- 공개 접근: 비공개(No Public Access)
- 저장 등급: Standard
- 리전: 인프라 Terraform의 `terraform.tfvars`에 설정된 `region`
- 보존: `issuebell/postgresql/` prefix의 객체는 7일 후 삭제

적용 방법과 Terraform 리소스는 [`terraform/objectstorage-backups/README.md`](../terraform/objectstorage-backups/README.md)를 참고하세요. Free Tier 포함량을 넘는 저장량이나 요청은 과금될 수 있으므로, 실제 백업 크기와 버킷 사용량을 확인하세요.

### OCI 인증 준비

업로더는 OCI CLI의 API Key 프로필을 사용합니다. Terraform 정책은 지정 API 사용자를 해당 버킷의 `issuebell/postgresql/` 경로에만 제한합니다. API 개인 키는 Git에 넣지 마세요.

WSL Ubuntu에서 `/oracle/.oci/oci_api_key.pem`에 대응하는 Secret 볼륨 파일을 준비합니다. OCI 식별자 값은 인프라 저장소의 `terraform/terraform.tfvars`에서 가져오고, 파일은 Git에 추가하지 않습니다.

```bash
cat > "$HOME/.oci/issuebell-backup-config" <<'EOF'
[DEFAULT]
user=<user_ocid>
fingerprint=<fingerprint>
tenancy=<tenancy_ocid>
region=<terraform.tfvars의 region>
key_file=/oracle/.oci/oci_api_key.pem
EOF
chmod 600 "$HOME/.oci/issuebell-backup-config"

kubectl -n issuebell create secret generic issuebell-backup-oci \
  --from-file=config="$HOME/.oci/issuebell-backup-config" \
  --from-file=oci_api_key.pem="$HOME/.oci/oci_api_key.pem" \
  --dry-run=client -o yaml | kubectl apply -f -
```

`<user_ocid>`, `<fingerprint>`, `<tenancy_ocid>`를 실제 값으로 바꿉니다. Config 파일의 `key_file` 경로는 컨테이너 안의 Secret 마운트 위치이므로 그대로 둡니다. 개인 키는 이 Secret을 통해 클러스터에 전달되며 저장소에는 커밋되지 않습니다.

### 배포 및 확인

Argo CD가 `k8s/overlays/production`을 동기화하면 ServiceAccount와 CronJob이 함께 배포됩니다. 첫 실행을 즉시 확인하려면:

```bash
kubectl -n issuebell create job --from=cronjob/issuebell-postgres-backup issuebell-backup-manual
kubectl -n issuebell logs -f job/issuebell-backup-manual -c dump-postgres
kubectl -n issuebell logs -f job/issuebell-backup-manual -c upload-to-object-storage
```

Object Storage에는 `issuebell/postgresql/issuebell-<UTC timestamp>.dump` 이름으로 백업이 쌓입니다. 복구할 때는 파일을 내려받은 뒤 PostgreSQL 16의 `pg_restore`를 사용합니다.

## 운영 Secret 생성

운영 Secret은 Git에 커밋하지 않습니다. `.env.production.example`을 복사해 실제 값을 입력한 뒤 다음을 실행합니다.

```bash
kubectl create namespace issuebell --dry-run=client -o yaml | kubectl apply -f -
kubectl -n issuebell create secret generic issuebell-secret \
  --from-env-file=.env.production \
  --dry-run=client -o yaml | kubectl apply -f -
```

## 배포 구조

- `base`: 모든 환경에서 공통으로 사용하는 앱, PostgreSQL StatefulSet, 서비스, PVC
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

## PostgreSQL 운영 주의사항

PostgreSQL은 안정적인 Pod 이름과 순차 재시작을 위해 단일 replica StatefulSet으로 실행합니다. 기존 `postgres-data` PVC를 명시적으로 계속 사용하므로 Deployment에서 전환할 때 새 빈 볼륨으로 바뀌지 않습니다. Argo CD는 새 리소스를 적용한 뒤 이전 Deployment를 제거하도록 `PruneLast=true`를 사용합니다.

StatefulSet은 백업이나 고가용성을 자동으로 제공하지 않습니다. 정기적인 `pg_dump`/스냅샷과 복구 훈련이 별도로 필요하며, 서비스 중요도가 높아지면 관리형 PostgreSQL이나 PostgreSQL Operator 기반 복제 구성을 권장합니다. 전환 전에는 반드시 PVC 스냅샷 또는 논리 백업을 생성하세요.
