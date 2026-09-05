# Matik - MySQL vs Postgres

Date: 2025-07-28

Status: `Accepted`

Collaborators: @julie-trias

## Context
For Matik, we have 2 options worth considering that are available to us via AWS RDS - Postgres and MySQL. Either of the two database systems can work for Matik.

### Option I - PostgreSQL
#### Pros
- Object-relational
    - PostgreSQL is an object-relational database management system (ORDBMS), supporting object-oriented concepts like table inheritance and custom data types, offering greater flexibility for complex data structures than MySQL's purely relational model
- Feature-rich
    - PostgreSQL boasts a more extensive set of features, including support for: advanced SQL features like window functions, common table expressions (CTEs), and a richer selection of data types (e.g., arrays, hstore, JSONB, geometric/GIS, network address types, UUIDs)
- Data integrity and reliability
    - PostgreSQL emphasizes data integrity, strictly adhering to ACID (Atomicity, Consistency, Isolation, Durability) principles and providing robust transaction support. Its MVCC (Multi-Version Concurrency Control) implementation is widely considered efficient for handling high-concurrency workloads
- Extensibility
    - PostgreSQL's robust extension system allows users to define custom functions, operators, and data types, including geospatial capabilities through the PostGIS extension
- Complex workloads
    - PostgreSQL is often preferred for applications requiring complex queries, analytical processing, or handling large datasets

#### Cons
- Complexity and Learning Curve
    - PostgreSQL can be more challenging to set up, configure, and manage, particularly for beginners or those unfamiliar with its features and architecture
- Performance (Read-Only or Read-Heavy Workloads)
    - For simple read-heavy workloads, PostgreSQL may not be as fast as MySQL due to its process-per-connection model and higher memory consumption per connection
- Tooling (Historically)
    - While this is improving, some sources point to PostgreSQL historically having less mature and user-friendly administration tools compared to some commercial databases or even MySQL
- Potential for Bloat
    - PostgreSQL's MVCC implementation requires periodic cleanup (VACUUM) to prevent database bloat, which can impact performance if not properly managed

### Option II - MySQL
#### Pros
- Ease of use and setup
    - MySQL is known for its simplicity, shorter learning curve, and ease of installation and configuration, especially for beginners
- Read-heavy workloads
    - MySQL traditionally excels in scenarios dominated by read-only operations, such as web applications and Content Management Systems (CMS). Its replication features are also well-suited for scaling out reads
- Cloud-readiness
    - MySQL is readily available as a managed service on various cloud platforms, facilitating deployment and management
- Large community and ecosystem
    - MySQL has a huge user base and community, which can make it easier to find resources, support, and developers with MySQL experience

#### Cons
- Limited Advanced Features
    - Compared to PostgreSQL, MySQL may lack some advanced SQL features, data types, and extensibility options that are beneficial for complex applications
- Concurrency Issues (Write-Heavy Workloads)
    - MySQL's use of write locks can lead to performance bottlenecks in environments with frequent concurrent write operations
- Less Extensible
    - While MySQL supports features like JSON data types, its extensibility and ability to handle complex data types are less comprehensive than PostgreSQL's
- Potential for Data Corruption (MyISAM Engine)
    While the InnoDB engine is ACID compliant, older storage engines like MyISAM do not support ACID properties, increasing the risk of data corruption
- Licensing and Development Control
    - Oracle's acquisition and maintenance of MySQL have raised concerns about its open-source nature and potential influence on development

## Decision
MySQL.

## Considerations
- The decision to use MySQL is its ability to support read-heavy workloads since Matik serves as an AI Ops catalogue with many other services reading data from it. Aurora MySQL can deliver up to five times the throughput of standard MySQL, while Aurora PostgreSQL can achieve up to three times the throughput of standard PostgreSQL. This performance boost comes from Aurora's cloud-native architecture, which separates compute and storage, optimizes caching, and utilizes distributed systems techniques for I/O.

## Consequences
A much higher read throughput.
