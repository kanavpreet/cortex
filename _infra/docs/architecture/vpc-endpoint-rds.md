>
> :warning: This will be deprecated as soon as we fully migrate to the RDS cluster that is in the Prod Environment
>

# VPC Endpoint Architecture for Cross-Account RDS Access

## Data Flow Diagram

```mermaid
graph TB
    subgraph "airbnb-prod Account"
        App[Application]
        VPCEndpoint[VPC Endpoint<br/>vpce-xxx.vpce.amazonaws.com]
    end

    subgraph "corpinfra-prod Account"
        subgraph "VPC Endpoint Service"
            EPService[VPC Endpoint Service<br/>com.amazonaws.vpce.us-east-1.vpce-svc-xxx]
        end

        subgraph "Network Load Balancer"
            NLB[NLB: matik-dev<br/>Internal Load Balancer]
            TG[Target Group<br/>Port 3306]
        end

        subgraph "Lambda Automation"
            Lambda[Lambda Function<br/>rds-ip-updater]
            EventBridge[EventBridge Rule<br/>Triggers every 5 min]
        end

        subgraph "RDS Aurora MySQL"
            RDS1[Primary Instance<br/>IP: 10.x.x.x]
            RDS2[Replica Instance<br/>IP: 10.x.x.y]
            RDSEndpoint[Cluster Endpoint<br/>matik-dev.cluster-xxx.rds.amazonaws.com]
        end

        DNS[Infoblox DNS<br/>matik-db-dev.airbnb.biz]
    end

    subgraph "Engineer Access"
        Engineer[Engineer via VPN]
    end

    %% Data Flow
    App -->|MySQL 3306| VPCEndpoint
    VPCEndpoint -->|PrivateLink| EPService
    EPService -->|Routes to| NLB
    NLB -->|Forwards to| TG
    TG -->|Port 3306| RDS1

    %% Lambda Flow
    EventBridge -.->|Triggers| Lambda
    Lambda -.->|Resolves cluster endpoint| RDSEndpoint
    Lambda -.->|Registers/Deregisters IPs| TG
    RDS1 -.->|Primary IP: 10.x.x.x| Lambda

    %% Engineer Flow
    Engineer -->|MySQL 3306| DNS
    DNS -->|CNAME| RDSEndpoint
    RDSEndpoint -->|Direct| RDS1

    class App,VPCEndpoint prodAccount
    class NLB,TG,EPService corpAccount
    class Lambda,EventBridge automation
    class RDS1,RDS2,RDSEndpoint database
```

## Component Description

### 1. Application Connection (airbnb-prod → RDS)
- **Application** in airbnb-prod connects to **VPC Endpoint** DNS name
- **VPC Endpoint** uses AWS PrivateLink to connect to **VPC Endpoint Service** in corpinfraprod
- **VPC Endpoint Service** is backed by the **Network Load Balancer**
- **NLB** forwards traffic to **Target Group** containing RDS IP addresses
- **Target Group** routes to actual **RDS instances**

### 2. Lambda Automation
- **EventBridge Rule** triggers Lambda every 5 minutes
- **Lambda Function** resolves the RDS **cluster endpoint** (primary instance) using Python's `socket.gethostbyname_ex()` to get the current primary IP
- Lambda queries AWS to get current IPs in Target Group
- Lambda compares and **registers new IPs** / **deregisters old IPs** automatically
- Ensures Target Group always points to the current primary instance, even after failover
- **Note**: The cluster endpoint only resolves to the primary instance for writes

### 3. Engineer Access (Direct)
- **Engineers** connect via VPN to **Infoblox DNS** entry
- DNS CNAME points directly to **RDS Cluster Endpoint** (not NLB)
- Provides direct database access for development/debugging

```

## Resources Created

| Resource | Purpose |
|----------|---------|
| Network Load Balancer | Routes traffic to RDS instances |
| Target Group | Contains RDS IP addresses |
| VPC Endpoint Service | Exposes NLB via PrivateLink |
| Lambda Function | Auto-updates RDS IPs in target group |
| EventBridge Rule | Triggers Lambda every 5 minutes |
| IAM Role & Policy | Grants Lambda permissions |
| Infoblox CNAME | DNS entry for engineer access |
