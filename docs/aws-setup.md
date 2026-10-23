# Setup na AWS (via CLI)

Comandos para criar toda a infraestrutura da semana 1 na mão. Na semana 2 tudo isso vira Terraform.

> **Dica:** rode no **AWS CloudShell** (console da AWS, ícone `>_`). Ele já tem AWS CLI, Docker, `jq` e suas credenciais. Se preferir local, use Linux, macOS ou WSL com a AWS CLI v2 configurada (`aws configure`).

Todos os comandos assumem a região `us-east-1` e o nome `tasks-api`. Se mudar, ajuste também `ecs/task-definition.json` e `.github/workflows/ci-cd.yml`.

## 0. Variáveis

```bash
export AWS_REGION=us-east-1
export APP=tasks-api
export CLUSTER=tasks-cluster
export SERVICE=tasks-api-service
export ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
export ECR_URI=$ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com/$APP
export GH_REPO="SEU_USUARIO/SEU_REPO"   # ex.: kevenmelo/ecs-fargate-cicd
```

## 1. ECR, secret e logs

```bash
aws ecr create-repository --repository-name $APP \
  --image-scanning-configuration scanOnPush=true --region $AWS_REGION

aws secretsmanager create-secret --name $APP/api-key \
  --secret-string "$(openssl rand -hex 16)" --region $AWS_REGION

aws logs create-log-group --log-group-name /ecs/$APP --region $AWS_REGION
aws logs put-retention-policy --log-group-name /ecs/$APP --retention-in-days 7
```

## 2. Role de execução da task

É a role que o ECS usa para baixar a imagem do ECR, ler o secret e escrever logs.

```bash
cat > trust-ecs.json <<'EOF'
{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"ecs-tasks.amazonaws.com"},"Action":"sts:AssumeRole"}]}
EOF

aws iam create-role --role-name ${APP}-execution-role \
  --assume-role-policy-document file://trust-ecs.json

aws iam attach-role-policy --role-name ${APP}-execution-role \
  --policy-arn arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy

SECRET_ARN=$(aws secretsmanager describe-secret --secret-id $APP/api-key \
  --query ARN --output text)

aws iam put-role-policy --role-name ${APP}-execution-role --policy-name read-api-key \
  --policy-document "{\"Version\":\"2012-10-17\",\"Statement\":[{\"Effect\":\"Allow\",\"Action\":\"secretsmanager:GetSecretValue\",\"Resource\":\"$SECRET_ARN\"}]}"
```

## 3. Rede (VPC default) e security groups

Na semana 1 usamos a VPC default para focar em ECS. As tasks recebem IP público para baixar a imagem **sem NAT Gateway** (que custa caro), mas só aceitam tráfego vindo do ALB. Na semana 2 você cria uma VPC própria com subnets privadas.

```bash
VPC_ID=$(aws ec2 describe-vpcs --filters Name=is-default,Values=true \
  --query 'Vpcs[0].VpcId' --output text)
SUBNET_IDS=$(aws ec2 describe-subnets \
  --filters Name=vpc-id,Values=$VPC_ID Name=default-for-az,Values=true \
  --query 'Subnets[].SubnetId' --output text)
SUBNETS_CSV=$(echo $SUBNET_IDS | tr ' ' ',')

ALB_SG=$(aws ec2 create-security-group --group-name ${APP}-alb-sg \
  --description "ALB publico" --vpc-id $VPC_ID --query GroupId --output text)
aws ec2 authorize-security-group-ingress --group-id $ALB_SG \
  --protocol tcp --port 80 --cidr 0.0.0.0/0

TASK_SG=$(aws ec2 create-security-group --group-name ${APP}-task-sg \
  --description "Tasks ECS" --vpc-id $VPC_ID --query GroupId --output text)
aws ec2 authorize-security-group-ingress --group-id $TASK_SG \
  --protocol tcp --port 8000 --source-group $ALB_SG
```

## 4. Application Load Balancer

```bash
ALB_ARN=$(aws elbv2 create-load-balancer --name ${APP}-alb \
  --subnets $SUBNET_IDS --security-groups $ALB_SG \
  --query 'LoadBalancers[0].LoadBalancerArn' --output text)

TG_ARN=$(aws elbv2 create-target-group --name ${APP}-tg \
  --protocol HTTP --port 8000 --vpc-id $VPC_ID --target-type ip \
  --health-check-path /health \
  --query 'TargetGroups[0].TargetGroupArn' --output text)

aws elbv2 create-listener --load-balancer-arn $ALB_ARN --protocol HTTP --port 80 \
  --default-actions Type=forward,TargetGroupArn=$TG_ARN

ALB_DNS=$(aws elbv2 describe-load-balancers --load-balancer-arns $ALB_ARN \
  --query 'LoadBalancers[0].DNSName' --output text)
echo "URL da API: http://$ALB_DNS"
```

## 5. Primeira imagem (bootstrap)

O serviço ECS precisa de uma imagem existente para subir, e o pipeline só faz deploy num serviço que já existe. Por isso a primeira imagem é enviada na mão. Dentro da pasta do projeto:

```bash
aws ecr get-login-password --region $AWS_REGION | \
  docker login --username AWS --password-stdin $ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com

docker build --platform linux/amd64 -t $ECR_URI:bootstrap .
docker push $ECR_URI:bootstrap
```

> O `--platform linux/amd64` evita o erro `exec format error` ao buildar em Mac com chip Apple Silicon, já que a task definition usa `X86_64`.

## 6. Cluster, task definition e serviço

```bash
aws ecs create-cluster --cluster-name $CLUSTER

sed "s/<ACCOUNT_ID>/$ACCOUNT_ID/g" ecs/task-definition.json \
  | jq --arg img "$ECR_URI:bootstrap" '.containerDefinitions[0].image=$img' \
  > taskdef.rendered.json

aws ecs register-task-definition --cli-input-json file://taskdef.rendered.json

aws ecs create-service \
  --cluster $CLUSTER --service-name $SERVICE \
  --task-definition $APP --desired-count 2 --launch-type FARGATE \
  --network-configuration "awsvpcConfiguration={subnets=[$SUBNETS_CSV],securityGroups=[$TASK_SG],assignPublicIp=ENABLED}" \
  --load-balancers targetGroupArn=$TG_ARN,containerName=$APP,containerPort=8000 \
  --deployment-configuration "deploymentCircuitBreaker={enable=true,rollback=true},maximumPercent=200,minimumHealthyPercent=100" \
  --health-check-grace-period-seconds 30

aws ecs wait services-stable --cluster $CLUSTER --services $SERVICE
```

Teste:

```bash
curl http://$ALB_DNS/health
curl http://$ALB_DNS/

# escrita exige a API key guardada no Secrets Manager
API_KEY=$(aws secretsmanager get-secret-value --secret-id $APP/api-key \
  --query SecretString --output text)
curl -X POST http://$ALB_DNS/tasks \
  -H "Content-Type: application/json" -H "X-API-Key: $API_KEY" \
  -d '{"title":"primeira task"}'
```

> **Atenção:** como o armazenamento é em memória e há 2 tasks, um `GET /tasks` pode retornar vazio ou a lista, dependendo de qual task o ALB escolher. Isso é esperado e é o gancho para adicionar um banco de dados depois.

## 7. GitHub Actions com OIDC (sem chaves de acesso)

```bash
aws iam create-open-id-connect-provider \
  --url https://token.actions.githubusercontent.com \
  --client-id-list sts.amazonaws.com
```

Role que **somente a branch `main` do seu repositório** pode assumir:

```bash
cat > trust-github.json <<EOF
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Principal": {"Federated": "arn:aws:iam::$ACCOUNT_ID:oidc-provider/token.actions.githubusercontent.com"},
    "Action": "sts:AssumeRoleWithWebIdentity",
    "Condition": {
      "StringEquals": {"token.actions.githubusercontent.com:aud": "sts.amazonaws.com"},
      "StringLike":   {"token.actions.githubusercontent.com:sub": "repo:$GH_REPO:ref:refs/heads/main"}
    }
  }]
}
EOF

aws iam create-role --role-name ${APP}-github-deploy \
  --assume-role-policy-document file://trust-github.json
```

Permissões mínimas para publicar a imagem e atualizar o serviço:

```bash
cat > deploy-policy.json <<EOF
{
  "Version": "2012-10-17",
  "Statement": [
    {"Effect": "Allow", "Action": "ecr:GetAuthorizationToken", "Resource": "*"},
    {"Effect": "Allow",
     "Action": ["ecr:BatchCheckLayerAvailability","ecr:InitiateLayerUpload","ecr:UploadLayerPart","ecr:CompleteLayerUpload","ecr:PutImage","ecr:BatchGetImage"],
     "Resource": "arn:aws:ecr:$AWS_REGION:$ACCOUNT_ID:repository/$APP"},
    {"Effect": "Allow",
     "Action": ["ecs:RegisterTaskDefinition","ecs:DescribeTaskDefinition"],
     "Resource": "*"},
    {"Effect": "Allow",
     "Action": ["ecs:UpdateService","ecs:DescribeServices"],
     "Resource": "arn:aws:ecs:$AWS_REGION:$ACCOUNT_ID:service/$CLUSTER/$SERVICE"},
    {"Effect": "Allow", "Action": "iam:PassRole",
     "Resource": "arn:aws:iam::$ACCOUNT_ID:role/${APP}-execution-role",
     "Condition": {"StringEquals": {"iam:PassedToService": "ecs-tasks.amazonaws.com"}}}
  ]
}
EOF

aws iam put-role-policy --role-name ${APP}-github-deploy \
  --policy-name deploy --policy-document file://deploy-policy.json

echo "Salve no GitHub (Settings > Secrets and variables > Actions) o secret AWS_ROLE_ARN:"
echo "arn:aws:iam::$ACCOUNT_ID:role/${APP}-github-deploy"
```

Se o pipeline falhar com `AccessDenied`, a mensagem de erro diz qual ação faltou. Ajustar a policy conforme isso faz parte do aprendizado.

## 8. (Opcional) Autoscaling

```bash
aws application-autoscaling register-scalable-target \
  --service-namespace ecs --scalable-dimension ecs:service:DesiredCount \
  --resource-id service/$CLUSTER/$SERVICE --min-capacity 1 --max-capacity 3

aws application-autoscaling put-scaling-policy --policy-name cpu-60 \
  --service-namespace ecs --scalable-dimension ecs:service:DesiredCount \
  --resource-id service/$CLUSTER/$SERVICE --policy-type TargetTrackingScaling \
  --target-tracking-scaling-policy-configuration '{"TargetValue":60.0,"PredefinedMetricSpecification":{"PredefinedMetricType":"ECSServiceAverageCPUUtilization"},"ScaleInCooldown":60,"ScaleOutCooldown":60}'
```

## 9. Destruir tudo (para não gerar custo)

Ordem importa por causa das dependências.

```bash
aws ecs update-service --cluster $CLUSTER --service $SERVICE --desired-count 0
aws ecs delete-service --cluster $CLUSTER --service $SERVICE --force
aws ecs delete-cluster --cluster $CLUSTER

LISTENER_ARN=$(aws elbv2 describe-listeners --load-balancer-arn $ALB_ARN \
  --query 'Listeners[0].ListenerArn' --output text)
aws elbv2 delete-listener --listener-arn $LISTENER_ARN
aws elbv2 delete-load-balancer --load-balancer-arn $ALB_ARN
sleep 60   # aguarda o ALB liberar as interfaces de rede
aws elbv2 delete-target-group --target-group-arn $TG_ARN

aws ec2 delete-security-group --group-id $TASK_SG
aws ec2 delete-security-group --group-id $ALB_SG

aws ecr delete-repository --repository-name $APP --force
aws secretsmanager delete-secret --secret-id $APP/api-key --force-delete-without-recovery
aws logs delete-log-group --log-group-name /ecs/$APP

aws iam delete-role-policy --role-name ${APP}-execution-role --policy-name read-api-key
aws iam detach-role-policy --role-name ${APP}-execution-role \
  --policy-arn arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy
aws iam delete-role --role-name ${APP}-execution-role
aws iam delete-role-policy --role-name ${APP}-github-deploy --policy-name deploy
aws iam delete-role --role-name ${APP}-github-deploy
```

Depois confira no console (ECS, EC2 > Load Balancers, ECR, Secrets Manager) se não sobrou nada.
