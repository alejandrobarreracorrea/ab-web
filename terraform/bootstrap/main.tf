# ---------------------------------------------------------------------------
# BOOTSTRAP (aplicar LOCALMENTE con credenciales admin, una sola vez):
# permisos que el rol OIDC del pipeline necesita para gestionar el stack de
# leads (ab-leads-*). Vive en un estado separado porque el propio pipeline
# no puede (ni debe) auto-otorgarse permisos.
#
#   cd terraform/bootstrap
#   terraform init \
#     -backend-config="bucket=essionix-s3-pdn-us-east-1-remotestates" \
#     -backend-config="key=ab-web/bootstrap/terraform.tfstate" \
#     -backend-config="region=us-east-1"
#   terraform apply
# ---------------------------------------------------------------------------
terraform {
  required_version = ">= 1.8.0"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
  backend "s3" {}
}

provider "aws" {
  region = "us-east-1"
}

data "aws_caller_identity" "current" {}

resource "aws_iam_role_policy" "ab_leads_deploy" {
  name = "ab-leads-deploy"
  role = "essionix-role-pdn-us-east-1-oidc"

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "LeadsLambda"
        Effect = "Allow"
        Action = "lambda:*"
        Resource = [
          "arn:aws:lambda:us-east-1:${data.aws_caller_identity.current.account_id}:function:ab-leads-*",
          "arn:aws:lambda:us-east-1:${data.aws_caller_identity.current.account_id}:function:ab-leads-*:*",
        ]
      },
      {
        Sid      = "LeadsDynamo"
        Effect   = "Allow"
        Action   = "dynamodb:*"
        Resource = "arn:aws:dynamodb:us-east-1:${data.aws_caller_identity.current.account_id}:table/ab-leads-*"
      },
      {
        Sid    = "LeadsIamRole"
        Effect = "Allow"
        Action = [
          "iam:CreateRole", "iam:DeleteRole", "iam:GetRole", "iam:TagRole", "iam:UntagRole",
          "iam:PutRolePolicy", "iam:GetRolePolicy", "iam:DeleteRolePolicy", "iam:ListRolePolicies",
          "iam:ListAttachedRolePolicies", "iam:AttachRolePolicy", "iam:DetachRolePolicy",
          "iam:ListInstanceProfilesForRole", "iam:UpdateAssumeRolePolicy", "iam:PassRole",
        ]
        Resource = "arn:aws:iam::${data.aws_caller_identity.current.account_id}:role/ab-leads-*"
      },
      {
        Sid      = "LeadsLogs"
        Effect   = "Allow"
        Action   = "logs:*"
        Resource = "arn:aws:logs:us-east-1:${data.aws_caller_identity.current.account_id}:log-group:/aws/lambda/ab-leads-*"
      },
      {
        Sid      = "LeadsSes"
        Effect   = "Allow"
        Action   = "ses:*"
        Resource = "arn:aws:ses:us-east-1:${data.aws_caller_identity.current.account_id}:identity/alejandrobarrera.net"
      },
    ]
  })
}

# Permisos del stack de pago de la mentoria (ab-pagos-*). Mismo criterio que
# ab-leads: lambda/dynamodb/iam/logs scopeados al prefijo, mas ssm (secret de
# credenciales Wompi/Cal.com) y events (cron EventBridge de pagos-liberar).
resource "aws_iam_role_policy" "ab_pagos_deploy" {
  name = "ab-pagos-deploy"
  role = "essionix-role-pdn-us-east-1-oidc"

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "PagosLambda"
        Effect = "Allow"
        Action = "lambda:*"
        Resource = [
          "arn:aws:lambda:us-east-1:${data.aws_caller_identity.current.account_id}:function:ab-pagos-*",
          "arn:aws:lambda:us-east-1:${data.aws_caller_identity.current.account_id}:function:ab-pagos-*:*",
        ]
      },
      {
        Sid      = "PagosDynamo"
        Effect   = "Allow"
        Action   = "dynamodb:*"
        Resource = "arn:aws:dynamodb:us-east-1:${data.aws_caller_identity.current.account_id}:table/ab-pagos-*"
      },
      {
        Sid    = "PagosIamRole"
        Effect = "Allow"
        Action = [
          "iam:CreateRole", "iam:DeleteRole", "iam:GetRole", "iam:TagRole", "iam:UntagRole",
          "iam:PutRolePolicy", "iam:GetRolePolicy", "iam:DeleteRolePolicy", "iam:ListRolePolicies",
          "iam:ListAttachedRolePolicies", "iam:AttachRolePolicy", "iam:DetachRolePolicy",
          "iam:ListInstanceProfilesForRole", "iam:UpdateAssumeRolePolicy", "iam:PassRole",
        ]
        Resource = "arn:aws:iam::${data.aws_caller_identity.current.account_id}:role/ab-pagos-*"
      },
      {
        Sid      = "PagosLogs"
        Effect   = "Allow"
        Action   = "logs:*"
        Resource = "arn:aws:logs:us-east-1:${data.aws_caller_identity.current.account_id}:log-group:/aws/lambda/ab-pagos-*"
      },
      {
        Sid      = "PagosSsm"
        Effect   = "Allow"
        Action   = "ssm:*"
        Resource = "arn:aws:ssm:us-east-1:${data.aws_caller_identity.current.account_id}:parameter/ab-pagos/*"
      },
      {
        Sid      = "PagosEvents"
        Effect   = "Allow"
        Action   = "events:*"
        Resource = "arn:aws:events:us-east-1:${data.aws_caller_identity.current.account_id}:rule/ab-pagos-*"
      },
    ]
  })
}
