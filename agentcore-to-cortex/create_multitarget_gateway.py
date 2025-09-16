#!/usr/bin/env python3
import os
import json
from bedrock_agentcore_starter_toolkit.operations.gateway.client import GatewayClient

# https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/create-gateway-methods.html

def create_multi_target_gateway():
    """Create gateway with both Snowflake Cortex and Wikipedia targets"""
    
    # Load OpenAPI schemas from files
    with open("cortex_agents_openapi.json", "r") as f:
        cortex_openapi_schema = json.load(f)
    
    with open("wikipedia_openapi.json", "r") as f:
        wikipedia_openapi_schema = json.load(f)
    
    # Configuration
    region = os.environ.get("AWS_DEFAULT_REGION", "us-west-2")
    gateway_name = f"MultiTargetGateway-{os.urandom(4).hex()}"
    pat_token = f"Bearer {os.environ['SNOWFLAKE_PAT_TOKEN']}"
    
    print(f"Creating multi-target gateway:")
    print(f"  Name: {gateway_name}")
    print(f"  Region: {region}")
    print(f"  Targets: Snowflake Cortex + Wikipedia")
    
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
        
        # Step 3: Add Snowflake Cortex target
        print("🎯 Adding Snowflake Cortex target...")
        cortex_target_response = client.create_mcp_gateway_target(
            gateway=gateway_response,
            name="SnowflakeCortexTarget",
            target_type="openApiSchema",
            target_payload={"inlinePayload": json.dumps(cortex_openapi_schema)}, 
            credentials={
                "api_key": pat_token,
                "credential_location": "HEADER",
                "credential_parameter_name": "Authorization"
            }
        )
        
        # Step 4: Add Wikipedia target
        print("📚 Adding Wikipedia target...")
        wikipedia_target_response = client.create_mcp_gateway_target(
            gateway=gateway_response,
            name="WikipediaTarget",
            target_type="openApiSchema",
            target_payload={"inlinePayload": json.dumps(wikipedia_openapi_schema)},
            credentials={
                "api_key": "AgentCore-Gateway/1.0 (AWS-Bedrock-AgentCore) Python/requests",
                "credential_location": "HEADER",
                "credential_parameter_name": "User-Agent"
            }
        )
        
        # Step 5: Get access token
        print("🎫 Getting access token...")
        access_token = client.get_access_token_for_cognito(cognito_response["client_info"])
        
        print("✅ Multi-target gateway created!")
        print(f"Gateway ID: {gateway_response['gatewayId']}")
        print(f"Gateway URL: {gateway_response['gatewayUrl']}")
        print(f"Cortex Target ID: {cortex_target_response.get('targetId', 'N/A')}")
        print(f"Wikipedia Target ID: {wikipedia_target_response.get('targetId', 'N/A')}")
        print("\nAvailable tools:")
        print("  - SnowflakeCortexTarget___runAgent (for Cortex queries)")
        print("  - WikipediaTarget___getPageSummary (for Wikipedia summaries)")
        print("  - WikipediaTarget___getPageMedia (for Wikipedia media)")
        
        # Save to settings.json
        settings = {
            "gateway_id": gateway_response["gatewayId"],
            "gateway_url": gateway_response["gatewayUrl"],
            "access_token": access_token,
            "client_info": cognito_response["client_info"],
            "region": region,
            "targets": {
                "cortex": {
                    "target_id": cortex_target_response.get('targetId'),
                    "name": "SnowflakeCortexTarget",
                    "tools": ["SnowflakeCortexTarget___runAgent"]
                },
                "wikipedia": {
                    "target_id": wikipedia_target_response.get('targetId'),
                    "name": "WikipediaTarget",
                    "tools": [
                        "WikipediaTarget___getPageSummary",
                        "WikipediaTarget___getPageMedia"
                    ]
                }
            }
        }
        
        with open("settings.json", "w") as f:
            json.dump(settings, f, indent=2)
        
        print("✅ Settings saved to settings.json")
        
        # Example usage
        print("\n💡 Example usage:")
        print('Ask: "What are the ratings for Toy Story and also give me a Wikipedia summary of Toy Story?"')
        print("The gateway will:")
        print("  1. Query Cortex for movie ratings data")
        print("  2. Query Wikipedia for Toy Story summary")
        print("  3. Combine both results in the response")
        
    except Exception as e:
        print(f"❌ Error: {e}")
        raise

if __name__ == "__main__":
    create_multi_target_gateway()
