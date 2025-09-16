#!/usr/bin/env python3
"""
AWS Cleanup Script for Bedrock AgentCore Gateway Resources

This script deletes all AWS resources created by the gateway setup:
- Bedrock AgentCore Gateways
- Cognito User Pools, Domains, Resource Servers, and Clients
- IAM Roles (AgentCore related)
- Lambda Functions (gateway proxy functions)

Usage:
    python cleanup_aws_resources.py [--region us-west-2] [--dry-run]
"""

import argparse
import json
import logging
import time
from pathlib import Path
from typing import List, Dict, Any, Optional

import boto3
from botocore.exceptions import ClientError


class AWSResourceCleanup:
    """Cleanup AWS resources created by Bedrock AgentCore Gateway setup."""
    
    def __init__(self, region: str = "us-west-2", dry_run: bool = False):
        """Initialize cleanup client.
        
        Args:
            region: AWS region to clean up resources in
            dry_run: If True, only show what would be deleted without actually deleting
        """
        self.region = region
        self.dry_run = dry_run
        self.session = boto3.Session(region_name=region)
        
        # Initialize clients
        self.bedrock_client = self.session.client("bedrock-agentcore-control")
        self.cognito_client = self.session.client("cognito-idp")
        self.iam_client = self.session.client("iam")
        self.lambda_client = self.session.client("lambda")
        self.secretsmanager_client = self.session.client("secretsmanager")
        
        # Setup logging
        logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
        self.logger = logging.getLogger(__name__)
        
        if dry_run:
            self.logger.info("🔍 DRY RUN MODE - No resources will actually be deleted")

    def load_settings_file(self) -> Optional[Dict[str, Any]]:
        """Load settings from settings.json if it exists."""
        settings_path = Path("settings.json")
        if settings_path.exists():
            try:
                with open(settings_path, 'r') as f:
                    settings = json.load(f)
                self.logger.info(f"📄 Loaded settings from {settings_path}")
                return settings
            except Exception as e:
                self.logger.warning(f"⚠️  Failed to load settings.json: {e}")
        return None

    def cleanup_bedrock_gateways(self) -> List[str]:
        """Delete all Bedrock AgentCore gateways."""
        deleted_gateways = []
        
        try:
            self.logger.info("🚪 Looking for Bedrock AgentCore Gateways...")
            
            # List all gateways
            response = self.bedrock_client.list_gateways()
            gateways = response.get("gateways", [])
            
            if not gateways:
                self.logger.info("   No gateways found")
                return deleted_gateways
            
            for gateway in gateways:
                gateway_id = gateway.get("gatewayId")
                gateway_name = gateway.get("name", "Unknown")
                gateway_arn = gateway.get("gatewayArn", "")
                
                # Look for AgentCore-related gateways (including multi-target ones)
                if (gateway_id and 
                    ('gateway' in gateway_name.lower() or 
                     'cortex' in gateway_name.lower() or
                     'multitarget' in gateway_name.lower() or
                     'agentcore' in gateway_name.lower())):
                    
                    self.logger.info(f"   Found AgentCore gateway: {gateway_name} ({gateway_id})")
                    
                    if not self.dry_run:
                        try:
                            # Delete gateway targets first (including Wikipedia targets)
                            self._delete_gateway_targets(gateway_arn)
                            
                            # Delete the gateway
                            self.bedrock_client.delete_gateway(gatewayId=gateway_id)
                            self.logger.info(f"   ✅ Deleted gateway: {gateway_name}")
                            deleted_gateways.append(gateway_id)
                        except ClientError as e:
                            self.logger.error(f"   ❌ Failed to delete gateway {gateway_name}: {e}")
                    else:
                        self.logger.info(f"   🔍 Would delete gateway: {gateway_name}")
                        deleted_gateways.append(gateway_id)
                        
        except ClientError as e:
            if e.response['Error']['Code'] == 'AccessDeniedException':
                self.logger.warning("⚠️  No access to Bedrock AgentCore - skipping gateway cleanup")
            else:
                self.logger.error(f"❌ Error listing gateways: {e}")
                
        return deleted_gateways

    def _delete_gateway_targets(self, gateway_arn: str):
        """Delete all targets for a gateway (including Cortex and Wikipedia targets)."""
        try:
            response = self.bedrock_client.list_gateway_targets(gatewayArn=gateway_arn)
            targets = response.get("targets", [])
            
            for target in targets:
                target_id = target.get("targetId")
                target_name = target.get("name", "Unknown")
                
                if target_id:
                    self.logger.info(f"     Deleting target: {target_name} ({target_id})")
                    
                    # Delete credential providers associated with this target
                    self._delete_target_credentials(gateway_arn, target_id)
                    
                    # Delete the target itself
                    self.bedrock_client.delete_gateway_target(
                        gatewayArn=gateway_arn,
                        targetId=target_id
                    )
                    self.logger.info(f"     ✅ Deleted target: {target_name}")
        except ClientError as e:
            self.logger.warning(f"     ⚠️  Failed to delete targets: {e}")

    def _delete_target_credentials(self, gateway_arn: str, target_id: str):
        """Delete credential providers for a target."""
        try:
            # List credential providers for this target
            response = self.bedrock_client.list_credential_providers(gatewayArn=gateway_arn)
            providers = response.get("credentialProviders", [])
            
            for provider in providers:
                provider_arn = provider.get("credentialProviderArn", "")
                provider_name = provider.get("name", "Unknown")
                
                # Check if this provider is related to our targets
                if (provider_arn and 
                    ('snowflakecortextarget' in provider_name.lower() or
                     'wikipediatarget' in provider_name.lower() or
                     'apikey' in provider_name.lower())):
                    
                    self.logger.info(f"       Deleting credential provider: {provider_name}")
                    self.bedrock_client.delete_credential_provider(
                        credentialProviderArn=provider_arn
                    )
        except ClientError as e:
            self.logger.debug(f"       Could not delete credential providers: {e}")

    def cleanup_cognito_resources(self) -> List[str]:
        """Delete all Cognito resources created by AgentCore."""
        deleted_pools = []
        
        try:
            self.logger.info("🔐 Looking for Cognito User Pools...")
            
            # List all user pools
            paginator = self.cognito_client.get_paginator('list_user_pools')
            
            for page in paginator.paginate(MaxResults=60):
                user_pools = page.get('UserPools', [])
                
                for pool in user_pools:
                    pool_id = pool.get('Id')
                    pool_name = pool.get('Name', '')
                    
                    # Look for AgentCore-related pools (including multi-target gateways)
                    if ('agentcore' in pool_name.lower() or 
                        'gateway' in pool_name.lower() or
                        'multitarget' in pool_name.lower()):
                        self.logger.info(f"   Found AgentCore pool: {pool_name} ({pool_id})")
                        
                        if not self.dry_run:
                            try:
                                # Delete user pool domain first (if exists)
                                self._delete_user_pool_domain(pool_id)
                                
                                # Delete the user pool (this also deletes clients and resource servers)
                                self.cognito_client.delete_user_pool(UserPoolId=pool_id)
                                self.logger.info(f"   ✅ Deleted user pool: {pool_name}")
                                deleted_pools.append(pool_id)
                            except ClientError as e:
                                self.logger.error(f"   ❌ Failed to delete pool {pool_name}: {e}")
                        else:
                            self.logger.info(f"   🔍 Would delete user pool: {pool_name}")
                            deleted_pools.append(pool_id)
                            
        except ClientError as e:
            self.logger.error(f"❌ Error listing Cognito user pools: {e}")
            
        return deleted_pools

    def _delete_user_pool_domain(self, user_pool_id: str):
        """Delete user pool domain if it exists."""
        try:
            # Try to describe the domain to see if it exists
            response = self.cognito_client.describe_user_pool(UserPoolId=user_pool_id)
            domain = response.get('UserPool', {}).get('Domain')
            
            if domain:
                self.logger.info(f"     Deleting domain: {domain}")
                self.cognito_client.delete_user_pool_domain(
                    Domain=domain,
                    UserPoolId=user_pool_id
                )
                # Wait a bit for domain deletion to complete
                time.sleep(2)
        except ClientError as e:
            # Domain might not exist, that's okay
            self.logger.debug(f"     No domain to delete or error: {e}")

    def cleanup_iam_roles(self) -> List[str]:
        """Delete IAM roles created by AgentCore."""
        deleted_roles = []
        
        try:
            self.logger.info("👤 Looking for AgentCore IAM Roles...")
            
            # List roles and look for AgentCore-related ones
            paginator = self.iam_client.get_paginator('list_roles')
            
            for page in paginator.paginate():
                roles = page.get('Roles', [])
                
                for role in roles:
                    role_name = role.get('RoleName', '')
                    
                    # Look for AgentCore-related roles (but exclude AWS service-linked roles)
                    if (('agentcore' in role_name.lower() or 
                         'gateway' in role_name.lower() or
                         role_name == 'AgentCoreGatewayExecutionRole') and
                        not role_name.startswith('AWS') and
                        not role_name.startswith('AmazonSageMaker') and
                        'ServiceRole' not in role_name):
                        
                        self.logger.info(f"   Found AgentCore role: {role_name}")
                        
                        if not self.dry_run:
                            try:
                                # Detach policies first
                                self._detach_role_policies(role_name)
                                
                                # Delete the role
                                self.iam_client.delete_role(RoleName=role_name)
                                self.logger.info(f"   ✅ Deleted role: {role_name}")
                                deleted_roles.append(role_name)
                            except ClientError as e:
                                self.logger.error(f"   ❌ Failed to delete role {role_name}: {e}")
                        else:
                            self.logger.info(f"   🔍 Would delete role: {role_name}")
                            deleted_roles.append(role_name)
                            
        except ClientError as e:
            self.logger.error(f"❌ Error listing IAM roles: {e}")
            
        return deleted_roles

    def _detach_role_policies(self, role_name: str):
        """Detach all policies from a role before deletion."""
        try:
            # List attached managed policies
            response = self.iam_client.list_attached_role_policies(RoleName=role_name)
            for policy in response.get('AttachedPolicies', []):
                policy_arn = policy.get('PolicyArn')
                if policy_arn:
                    self.logger.info(f"     Detaching policy: {policy_arn}")
                    self.iam_client.detach_role_policy(RoleName=role_name, PolicyArn=policy_arn)
            
            # List inline policies
            response = self.iam_client.list_role_policies(RoleName=role_name)
            for policy_name in response.get('PolicyNames', []):
                self.logger.info(f"     Deleting inline policy: {policy_name}")
                self.iam_client.delete_role_policy(RoleName=role_name, PolicyName=policy_name)
                
        except ClientError as e:
            self.logger.warning(f"     ⚠️  Error detaching policies: {e}")

    def cleanup_lambda_functions(self) -> List[str]:
        """Delete Lambda functions created by AgentCore."""
        deleted_functions = []
        
        try:
            self.logger.info("⚡ Looking for AgentCore Lambda Functions...")
            
            # List functions
            paginator = self.lambda_client.get_paginator('list_functions')
            
            for page in paginator.paginate():
                functions = page.get('Functions', [])
                
                for function in functions:
                    function_name = function.get('FunctionName', '')
                    
                    # Look for AgentCore-related functions
                    if ('gateway_proxy' in function_name.lower() or 
                        'agentcore' in function_name.lower()):
                        
                        self.logger.info(f"   Found AgentCore function: {function_name}")
                        
                        if not self.dry_run:
                            try:
                                self.lambda_client.delete_function(FunctionName=function_name)
                                self.logger.info(f"   ✅ Deleted function: {function_name}")
                                deleted_functions.append(function_name)
                            except ClientError as e:
                                self.logger.error(f"   ❌ Failed to delete function {function_name}: {e}")
                        else:
                            self.logger.info(f"   🔍 Would delete function: {function_name}")
                            deleted_functions.append(function_name)
                            
        except ClientError as e:
            self.logger.error(f"❌ Error listing Lambda functions: {e}")
            
        return deleted_functions

    def cleanup_secrets(self) -> List[str]:
        """Delete Secrets Manager secrets created by AgentCore."""
        deleted_secrets = []
        
        try:
            self.logger.info("🔑 Looking for AgentCore Secrets...")
            
            # List secrets
            paginator = self.secretsmanager_client.get_paginator('list_secrets')
            
            for page in paginator.paginate():
                secrets = page.get('SecretList', [])
                
                for secret in secrets:
                    secret_name = secret.get('Name', '')
                    secret_arn = secret.get('ARN', '')
                    
                    # Look for AgentCore-related secrets (including multi-target secrets)
                    if ('agentcore' in secret_name.lower() or 
                        'gateway' in secret_name.lower() or
                        'snowflakecortextarget' in secret_name.lower() or
                        'wikipediatarget' in secret_name.lower()):
                        
                        self.logger.info(f"   Found AgentCore secret: {secret_name}")
                        
                        if not self.dry_run:
                            try:
                                # Delete immediately without recovery window
                                self.secretsmanager_client.delete_secret(
                                    SecretId=secret_arn,
                                    ForceDeleteWithoutRecovery=True
                                )
                                self.logger.info(f"   ✅ Deleted secret: {secret_name}")
                                deleted_secrets.append(secret_name)
                            except ClientError as e:
                                if 'bedrock-agentcore-identity' in str(e):
                                    self.logger.info(f"   ℹ️  Secret {secret_name} is managed by AgentCore service (will be auto-cleaned)")
                                else:
                                    self.logger.error(f"   ❌ Failed to delete secret {secret_name}: {e}")
                        else:
                            self.logger.info(f"   🔍 Would delete secret: {secret_name}")
                            deleted_secrets.append(secret_name)
                            
        except ClientError as e:
            self.logger.error(f"❌ Error listing secrets: {e}")
            
        return deleted_secrets

    def cleanup_settings_file(self):
        """Remove the local settings.json file."""
        settings_path = Path("settings.json")
        if settings_path.exists():
            if not self.dry_run:
                settings_path.unlink()
                self.logger.info("📄 ✅ Deleted settings.json")
            else:
                self.logger.info("📄 🔍 Would delete settings.json")

    def run_cleanup(self):
        """Run the complete cleanup process."""
        self.logger.info(f"🧹 Starting AWS resource cleanup in region: {self.region}")
        
        # Load settings to get specific resource IDs if available
        settings = self.load_settings_file()
        
        # Track what was deleted
        results = {
            "gateways": [],
            "cognito_pools": [],
            "iam_roles": [],
            "lambda_functions": [],
            "secrets": []
        }
        
        # Run cleanup in order (dependencies first)
        results["gateways"] = self.cleanup_bedrock_gateways()
        results["cognito_pools"] = self.cleanup_cognito_resources()
        results["iam_roles"] = self.cleanup_iam_roles()
        results["lambda_functions"] = self.cleanup_lambda_functions()
        results["secrets"] = self.cleanup_secrets()
        
        # Clean up local files
        self.cleanup_settings_file()
        
        # Summary
        self.logger.info("🎯 Cleanup Summary:")
        total_deleted = 0
        for resource_type, items in results.items():
            count = len(items)
            total_deleted += count
            action = "Would delete" if self.dry_run else "Deleted"
            self.logger.info(f"   {action} {count} {resource_type}")
        
        if total_deleted == 0:
            self.logger.info("   No AgentCore resources found to clean up! 🎉")
        else:
            action = "would be deleted" if self.dry_run else "deleted"
            self.logger.info(f"   Total: {total_deleted} resources {action}")
        
        if self.dry_run:
            self.logger.info("🔍 This was a dry run - no resources were actually deleted")
            self.logger.info("🔍 Run without --dry-run to perform actual cleanup")


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Clean up AWS resources created by Bedrock AgentCore Gateway setup"
    )
    parser.add_argument(
        "--region",
        default="us-west-2",
        help="AWS region to clean up resources in (default: us-west-2)"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be deleted without actually deleting anything"
    )
    
    args = parser.parse_args()
    
    # Create and run cleanup
    cleanup = AWSResourceCleanup(region=args.region, dry_run=args.dry_run)
    cleanup.run_cleanup()


if __name__ == "__main__":
    main()
