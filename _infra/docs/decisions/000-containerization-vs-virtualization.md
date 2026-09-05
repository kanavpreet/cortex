# Matik - Containerization vs Virtualization

Date: 2025-07-28

Status: `Accepted`

Collaborators: @julie-trias

## Context
Matik is a software platform made up of 6 services. For context, refer to the [https://lucid.app/lucidchart/6c6d807b-ca61-4be7-b571-897e35bd9712/edit?viewport_loc=-459%2C-30%2C4353%2C3420%2CyJO_wFNQ_qPY&invitationId=inv_16bf17b0-a22f-4cdf-b908-9e90f06fc47c](Matik C4 document) to understand the microservices that make up the Matik Platform.

### Option I - Virtualization

#### Pros
- This is more stable than deploying to Citadel

#### Cons
- Higher Resource Overhead
    - Virtualization involves running multiple virtual machines (VMs) on a single physical server, each with its own full operating system (OS). This means each VM consumes its dedicated resources like CPU, memory, and storage, leading to higher resource overhead compared to containers Containers, by contrast, share the host OS kernel, resulting in significantly lighter footprints and more efficient resource utilization.
- Slower Startup Times
    - Booting a full OS for each VM takes time, leading to slower startup times for virtualized applications, notes SentinelOne. Containers, being lightweight and sharing the host OS kernel, can start up almost instantaneously. This difference in startup time is crucial for agile development and dynamic scaling needs
- Reduced Portability
    - VMs can face compatibility issues when migrated between different hardware architectures or hypervisor platforms due to their reliance on specific OS versions and configurations. Containers, designed for consistency across environments, offer greater portability and can run on any platform supporting the container engine, regardless of the underlying OS or hardware, according to SentinelOne.
- Less Scalable for Microservices
    - While VMs can be scaled, their heavier nature and slower startup times make them less ideal for rapid, granular scaling in microservices architectures. Containers, on the other hand, are tailored for microservices and enable quicker, more efficient scaling to meet fluctuating workloads
- Security Concerns (depending on perspective)
    - While VMs provide strong isolation due to each running its own OS, some sources highlight potential vulnerabilities in the hypervisor that could compromise multiple VMs on the same host. Containers, while offering process-level isolation, share the host OS kernel, meaning a vulnerability in the kernel could affect all containers on that system, notes LinkedIn.
- Management Complexity
    - While tools exist for both, managing and orchestrating a large number of VMs can be complex due to the need to manage each VM's OS and resources. Containers, particularly with orchestration tools like Kubernetes, can simplify management of large-scale deployments due to their lightweight nature and standardized packaging

### Option II - Containerization

#### Pros
- Portability and Consistency
    - Applications and all its dependencies, including libraries and configuration files, are packaged in a self-contained unit called a container
    - Containers are portable, allowing them to run consistently across local and varying cloud environments without needing modifications
    - Ensures the application works uniformly across different environments
- Scalability
    - Containers enable applications to be scaled up or down quickly and efficiently in response to varying workloads
    - Container orchestration tools like Kubernetes automate the deployment, scaling, and management of containers, ensuring optimal resource allocation and high availability, allowing businesses to handle fluctuating data volumes and demands with greater agility
- Isolation and Security
    - Each container operates in its isolated environment, preventing conflicts between applications and enhancing security
    - If one container is compromised, the isolation helps prevent the spread of malicious code to other containers or the host system
    - Containerization platforms often include security features and tools to define access controls and scan for vulnerabilities
- Faster Development and Deployment
    - Containerization streamlines DevOps workflows and enables faster continuous integration and continuous delivery (CI/CD) pipelines
    - The ability to quickly build, deploy, replicate, and destroy containers speeds up development cycles and allows for rapid delivery of new features and updates
    - This agility is particularly beneficial for adopting microservices architecture, where applications are broken down into smaller, independent services, each running in its own container, according to Emergent Software
- Ease of management
    - Container orchestration platforms simplify the management of containerized applications, automating tasks like installation, upgrades, rollbacks, monitoring, logging, and debugging
    - This automation reduces the manual effort required for managing large-scale container deployments and ensures smooth operation

#### Cons
- Citadel is not a stable container hosting infrastructure

## Decision
Matik is a platform made up of multiple microservices that will be deployed containerized in Kubernetes via a Helm chart.

## Considerations
A paved path to deploying containerized applications in a stable, production-ready environment.

## Consequences
Matik will be deployed containerized in Prod instead of Citadel. See [001-hosting-in-prod-vs-biztech](Hosting Matik in Prod) for more details.
