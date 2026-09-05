# Matik - Deployment in Prod vs Biztech

Date: 2025-07-28

Status: `Accepted`

Collaborators: @julie-trias

Superseded by: [000-containerized-vs-virtualized](Containerized vs Virtualized)

## Context
Matik is a software platform made up of 6 services. For context, refer to the [https://lucid.app/lucidchart/6c6d807b-ca61-4be7-b571-897e35bd9712/edit?viewport_loc=-459%2C-30%2C4353%2C3420%2CyJO_wFNQ_qPY&invitationId=inv_16bf17b0-a22f-4cdf-b908-9e90f06fc47c](Matik C4 document) to understand the microservices that make up the Matik Platform.

The decision stems from Matik's product requirements, which are:
- A reliable, production-ready containerization infrastructure
- Access to various LLMs that have been developed and made available internally
- Access to Backstage for service catalog
- Being able to support services built on top of Matik that are deployed both in Biztech and Prod

*Important: Although there are service specific network paths from Biztech to Prod (i.e. Biztech VPC to Telescope), there is no PAVED network path from Biztech to Prod for other services, but it is in scope of the Matik project.*

### Option I - Deploy in biztech
#### Pros
- The team is most familiar with hosting and maintaining services in the current Biztech infrastructure using the current tools

#### Cons
- No access from Biztech to LLMs in Prod even after a more than a year of trying
- Citadel is not a stable environment especially for hosting production ready workloads. For example,
    - Changes to Citadel have been pushed out without proper testing and notice, resulting in incidents
    - Consistent demands for Dendrite upgrades with arbitrary deadlines and no runbooks to follow
    - Numerous outages with Istio upgrades or Kubernetes version upgrades
- Citadel uses spot instances and does not support managed nodes of any on-demand EC2 instance types required for workloads that need dedicated hosts or high CPU and/or high memory
- Citadel nodes do not support taints and tolerations
- EC2 is not a viable option for Matik as it is a platform that is made up of multiple microservices that are containerized. See [000-containerized-vs-virtualized.md](A Microservices Architecture) for more details

### Option II - Deploy in prod
#### Pros
- A paved network path exists from Prod to Biztech
- Access to Facade (Interface to internal LLMs) is readily available
- A stable, production ready, well-supported container hosting infrastructure, as well as CI/CD and tooling exists
- Some of Matik's customers are already deployed in Prod such as SPOG and IMBot

#### Cons
- There is no PAVED network path from Biztech to Prod
- There is no paved path for deploying Biztech services in Prod

## Decision
The decision is to deploy in Prod based on Matik's hard requirements to have access to our LLMs and a stable, well-supported container hosting infrastructure.

![Matik C4 - Matik Infrastructure](https://github.airbnb.biz/Airbnb-ITX/obs-docs/assets/3/24f7dd97-d338-40ce-b904-c10eefe07a4a)

## Considerations
- Being able to support services built on Matik that are deployed in both Biztech AND Prod.

## Consequences
- Matik will be creating a paved path for deploying Biztech services in Prod, including a network path from Biztech to Matik in Prod
- A wider reach in terms of adoption and contribution, which means other teams can build chroniclers and state machines in Matik for data sources that we don't already support
- Services built on Matik's actionable intelligence and AI Ops capabilities will have the option to deploy to Prod to reach a wider customer base, which is the whole company
