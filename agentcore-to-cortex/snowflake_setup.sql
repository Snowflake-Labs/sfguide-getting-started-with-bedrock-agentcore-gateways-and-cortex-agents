-- =============================================
-- Snowflake Setup for Cortex Agents
-- =============================================
-- Run as ACCOUNTADMIN to create role and database
-- Then switch to the created role for data setup

-- ==============================================
-- PART 1: ACCOUNTADMIN SETUP
-- ==============================================

USE ROLE ACCOUNTADMIN;

-- Create role for Cortex Agents
CREATE ROLE IF NOT EXISTS cortex_agent_role 
   COMMENT = 'Role for Cortex Agents demo';

-- Grant role to your user (replace YOUR_USERNAME)
--GRANT ROLE cortex_agent_role TO USER YOUR_USERNAME;

-- Create database and grant ownership
CREATE DATABASE IF NOT EXISTS CORTEX_DEMO_DB
   COMMENT = 'Database for Cortex Agents demo';

GRANT OWNERSHIP ON DATABASE CORTEX_DEMO_DB TO ROLE cortex_agent_role COPY CURRENT GRANTS;
GRANT OWNERSHIP ON SCHEMA CORTEX_DEMO_DB.PUBLIC TO ROLE cortex_agent_role COPY CURRENT GRANTS;

-- Create warehouse
CREATE WAREHOUSE IF NOT EXISTS CORTEX_DEMO_WH WITH
   WAREHOUSE_SIZE = 'XSMALL'
   AUTO_SUSPEND = 60
   AUTO_RESUME = TRUE
   COMMENT = 'Warehouse for Cortex Agents demo';

GRANT OWNERSHIP ON WAREHOUSE CORTEX_DEMO_WH TO ROLE cortex_agent_role COPY CURRENT GRANTS;

-- ==============================================
-- PART 2: DATA SETUP (as cortex_agent_role)
-- ==============================================
USE ROLE cortex_agent_role;
USE DATABASE CORTEX_DEMO_DB;

-- Create demo tables with sample data
CREATE TABLE IF NOT EXISTS REVENUE_DATA (
    customer_id NUMBER NOT NULL,
    customer_name VARCHAR(100) NOT NULL,
    region VARCHAR(50) NOT NULL,
    product_category VARCHAR(50) NOT NULL,
    revenue NUMBER(15,2) NOT NULL,
    sales_date DATE NOT NULL,
    sales_rep VARCHAR(100) NOT NULL,
    deal_size VARCHAR(20) NOT NULL,
    customer_tier VARCHAR(20) NOT NULL,
    industry VARCHAR(50) NOT NULL,
    PRIMARY KEY (customer_id)
);

INSERT INTO REVENUE_DATA VALUES
    (1, 'Acme Corporation', 'North America', 'Software', 500000.00, '2024-01-15', 'John Smith', 'Large', 'Enterprise', 'Technology'),
    (2, 'Global Tech Inc', 'Europe', 'Hardware', 750000.00, '2024-02-20', 'Sarah Johnson', 'Large', 'Enterprise', 'Manufacturing'),
    (3, 'Innovation Labs', 'Asia Pacific', 'Cloud Services', 300000.00, '2024-03-10', 'Mike Chen', 'Medium', 'Growth', 'Healthcare'),
    (4, 'Future Systems', 'North America', 'Analytics', 450000.00, '2024-04-05', 'Lisa Brown', 'Large', 'Enterprise', 'Finance'),
    (5, 'Digital Solutions', 'Europe', 'AI/ML', 600000.00, '2024-05-12', 'David Wilson', 'Large', 'Enterprise', 'Retail'),
    (6, 'TechStart Inc', 'North America', 'Software', 125000.00, '2024-06-01', 'Emma Davis', 'Small', 'Startup', 'Technology'),
    (7, 'MegaCorp Industries', 'Asia Pacific', 'Hardware', 950000.00, '2024-07-15', 'James Lee', 'Large', 'Enterprise', 'Manufacturing'),
    (8, 'CloudFirst Ltd', 'Europe', 'Cloud Services', 275000.00, '2024-08-03', 'Anna Mueller', 'Medium', 'Growth', 'Finance'),
    (9, 'DataDriven Co', 'North America', 'Analytics', 380000.00, '2024-08-20', 'Carlos Rodriguez', 'Medium', 'Growth', 'Healthcare'),
    (10, 'AI Innovations', 'Asia Pacific', 'AI/ML', 720000.00, '2024-09-10', 'Yuki Tanaka', 'Large', 'Enterprise', 'Technology');

-- Create contract documents for Cortex Search
CREATE TABLE IF NOT EXISTS CONTRACT_DOCUMENTS_UNSTRUCTURED (
    document_id STRING,
    document_type STRING DEFAULT 'CONTRACT',
    document_content VARIANT,
    searchable_text TEXT,
    created_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP()
);

-- Insert demo contract documents
INSERT INTO CONTRACT_DOCUMENTS_UNSTRUCTURED (document_id, document_type, searchable_text) VALUES
    ('CONTRACT_001', 'SOFTWARE_LICENSE', 'Enterprise Software License Agreement with Acme Corporation for $500,000. Includes premium support, training for 50 users, custom integration services, and API access. Auto-renewal with 90-day notice. Payment terms Net 30. Covers advanced analytics platform with machine learning capabilities.'),
    ('CONTRACT_002', 'HARDWARE_SUPPLY', 'Hardware Supply Agreement with Global Tech Inc for $750,000. Includes 6 high-performance server units with 3-year warranty, maintenance support, and 24/7 technical assistance. Annual renewal option with volume discounts available.'),
    ('CONTRACT_003', 'CLOUD_SERVICES', 'Cloud Services Agreement with Innovation Labs for $300,000. Provides scalable cloud infrastructure, data analytics platform, real-time monitoring, and disaster recovery services. Includes dedicated account management and quarterly reviews.'),
    ('CONTRACT_004', 'CONSULTING', 'Professional Services Agreement with Future Systems for $450,000. Includes strategic consulting, implementation services, change management, and training programs. 6-month engagement with option to extend.'),
    ('CONTRACT_005', 'MAINTENANCE', 'Annual Maintenance Agreement with Digital Solutions for $150,000. Covers software updates, bug fixes, performance optimization, and priority support. Includes access to new feature releases and documentation updates.');

-- Create stage for semantic models
CREATE STAGE IF NOT EXISTS SEMANTIC_MODELS
  DIRECTORY = (ENABLE = TRUE)
  COMMENT = 'Stage for semantic model files';

-- Create Cortex Search service
CREATE CORTEX SEARCH SERVICE IF NOT EXISTS CONTRACT_SEARCH
ON searchable_text
ATTRIBUTES document_type, document_id
WAREHOUSE = CORTEX_DEMO_WH
TARGET_LAG = '1 hour'
AS (
    SELECT 
        searchable_text,
        document_type,
        document_id
    FROM CONTRACT_DOCUMENTS_UNSTRUCTURED
);

-- Grant permissions on demo objects
GRANT SELECT ON TABLE REVENUE_DATA TO ROLE cortex_demo_role;
GRANT SELECT ON TABLE CONTRACT_DOCUMENTS_UNSTRUCTURED TO ROLE cortex_demo_role;
GRANT USAGE ON CORTEX SEARCH SERVICE CONTRACT_SEARCH TO ROLE cortex_demo_role;
GRANT USAGE ON STAGE SEMANTIC_MODELS TO ROLE cortex_demo_role;

-- Next, upload the semantic model from your local directory
PUT file://revenue_model.yaml @SEMANTIC_MODELS AUTO_COMPRESS=FALSE;

-- ==============================================
-- PART 3: PAT Token Creation
-- ==============================================

-- Create PAT for the integration (replace <YOUR_USERNAME>)
--ALTER USER <YOUR_USERNAME> ADD PROGRAMMATIC ACCESS TOKEN cortex_demo_token DAYS_TO_EXPIRY = 30 ROLE_RESTRICTION = 'CORTEX_DEMO_ROLE';
-- ==============================================
-- Alternatively, create the PAT through Snowsight:
-- ==============================================
-- Run this in Snowsight UI or SnowSQL:
 -- 1. Go to Account Settings > Security > Personal Access Tokens
 -- 2. Click "Generate Token"
 -- 3. Name: "cortex_demo_token"
 -- 4. Lifetime: 30 days (or as needed)
 -- 5. Copy the generated token for use in AgentCore Gateway