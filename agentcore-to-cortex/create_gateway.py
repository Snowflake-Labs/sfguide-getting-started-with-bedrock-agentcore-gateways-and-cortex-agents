#!/usr/bin/env python3
import os
import json
from bedrock_agentcore_starter_toolkit.operations.gateway.client import GatewayClient

# https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/create-gateway-methods.html

def create_gateway():
    """Create gateway with inline OpenAPI schema"""
    
    # Load OpenAPI schema from file
    with open("cortex_agents_openapi.json", "r") as f:
        openapi_schema = json.load(f)
    
    # Configuration
    region = os.environ.get("AWS_DEFAULT_REGION", "us-west-2")
    gateway_name = f"CortexAgentsGateway-{os.urandom(4).hex()}"
    pat_token = f"Bearer {os.environ['SNOWFLAKE_PAT_TOKEN']}"
    
    print(f"Creating gateway with inline schema:")
    print(f"  Name: {gateway_name}")
    print(f"  Region: {region}")
    
    # Create gateway client
    client = GatewayClient(region_name=region)
    
    try:
        # Step 1: Create OAuth authorizer with Cognito
        print("🔐 Creating OAuth authorizer...")
        cognito_response = client.create_oauth_authorizer_with_cognito(gateway_name)
        
        # Step 2: Create gateway
        print("🚪 Creating gateway...")
        gateway_response = client.create_mcp_gateway(
            name=gateway_name, 
            authorizer_config=cognito_response["authorizer_config"]
        )
        
        # Step 3: Add Snowflake Cortex target with INLINE OpenAPI schema
        print("🎯 Adding Snowflake Cortex target with inline schema...")
        target_response = client.create_mcp_gateway_target(
            gateway=gateway_response,
            name="SnowflakeCortexTarget",
            target_type="openApiSchema",
            target_payload={"inlinePayload": json.dumps(openapi_schema)}, 
            credentials={
                "api_key": pat_token,
                "credential_location": "HEADER",
                "credential_parameter_name": "Authorization"
            }
        )
        
        # Step 4: Get access token
        print("🎫 Getting access token...")
        access_token = client.get_access_token_for_cognito(cognito_response["client_info"])
        
        print("✅ Gateway created!")
        print(f"Gateway ID: {gateway_response['gatewayId']}")
        print(f"Gateway URL: {gateway_response['gatewayUrl']}")
        print(f"Target ID: {target_response.get('targetId', 'N/A')}")
        
        # Save to settings.json
        settings = {
            "gateway_id": gateway_response["gatewayId"],
            "gateway_url": gateway_response["gatewayUrl"],
            "access_token": access_token,
            "client_info": cognito_response["client_info"],
            "region": region
        }
        
        with open("settings.json", "w") as f:
            json.dump(settings, f, indent=2)
        
        print("✅ Settings saved to settings.json")
        
    except Exception as e:
        print(f"❌ Error: {e}")

if __name__ == "__main__":
    create_gateway()

