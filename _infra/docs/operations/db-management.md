>
> :warning: This will be deprecated as soon as we fully migrate to the RDS cluster that is in the Prod Environment
>

# Database Management
This document will outline different operations procedures for managing the Matik database.

## Admin Access
To gain admin access to the Matik database, please reach out to the Matik team.

## Creating a developer database user
Developer access to the database is managed via individual database users. A developer database user is created and assigned the `developer` role.

Follow the steps below to create a developer database user:
1. Log into the database as an admin user.
2. Run the following SQL commands, replacing `<username>` and `<password>` with the desired values:
    ```sql
    -- Create the user
    CREATE USER '<username>'@'%' IDENTIFIED BY '<password>';

    -- Assign the developer role
    GRANT 'developer' TO '<username>';

    -- Set default role
    SET DEFAULT ROLE 'developer' TO '<username>';

    -- Enforce password change on first login
    ALTER USER '<username>'@'%' PASSWORD EXPIRE;
    ```
3. Save the password in 1Password and share it with the Matik team member who requested the user.
