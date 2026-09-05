# Getting Access to Matik

## Quick Start Checklist

- [ ] Request Gandalf permissions (Matik resources + AWS IAM role)
- [ ] Set up AWS credentials for ProdEng account
- [ ] Request LDAP group membership for database access (if needed)
- [ ] Install k tools and airtool
- [ ] Configure Kubernetes access

---

## 1. Gandalf Permissions

Request access to these resources via Gandalf:

1. **AWS IAM Role** - `airbnb-users:cell_user`
   - [Request access here](https://gandalf-lite.airbnb.tools/permissions/aws_cloud/iam_role/airbnb-users%3Acell_user?type=permissions_granted)
   - Required for Kubernetes resource access

2. **Matik Resources** - All Matik-specific permissions
   - [Search and request all Matik permissions](https://gandalf-lite.airbnb.tools/search?q=matik)

---

## 2. AWS Credentials Setup

Matik is deployed in the **ProdEng account**. You need AWS credentials to access all Matik resources.

**First time setup**: Follow the [IAM human user creation guide](https://developers.a.musta.ch/docs/default/component/security-docs/cloudinfra/iam-human-user-creation)

---
## 3. Database Access
See [database section under development](https://developers.a.musta.ch/catalog/default/component/matik/docs/development/database/) for access information.

---
## 3. Database Access (Local Development)
>
> :warning: This will be deprecated as soon as we fully migrate to the RDS cluster that is in the Prod Environment
>

To connect to the Matik RDS cluster locally, you need the following:
1. A local database user created for you, request the Matik team to create one.
2. Membership in the appropriate LDAP group for network access.

### LDAP Groups

- **`matik-dyn`** - Production group with full network access
  - Automatically includes all BizTech Operations Engineering members
  - Includes members from the adhoc group

- **`g_matik-adhoc`** - For external engineers
  - Contact any BizTech Operations Engineering team member to be added

>**Note**: If you're not on the BizTech Operations Engineering team, request to be added to `g_matik-adhoc`.

### MYSQL Databases
| Environment  | Hostname           | Database Name       | Description                     |
|--------------|--------------------|---------------------|---------------------------------|
| sandbox  | `matik-db-dev.airbnb.biz`      | `matik_dev`        | Sandbox database            |
| staging      | `matik-db-stage.airbnb.biz`    | `matik_staging`    | Staging database                |
| production   | `matik-db-prod.airbnb.biz`     | `matik_prod`       | Production database             |

### Connecting to the Database
You can connect using any MYSQL client, either GUI or command line.

Example command line connection:
```bash
mysql -h <hostname> -P 3306 -u <username> -p
```

---

## 4. Kubernetes Access

**Namespace pattern**: `{project-name}-{environment}`
- Example: `matik-sandbox`, `matik-production`

### Required Tools

Install these tools to interact with Matik services in Kubernetes:

- **[airtool](https://developers.a.musta.ch/docs/default/component/airtool/)** - Airbnb internal tools manager
- **[k tools](https://developers.a.musta.ch/docs/default/component/kube-gen/k-tools/)** - Kubernetes resource management
- **[cellauth](https://developers.a.musta.ch/docs/default/component/kube-system/runbooks/cellauth/)** - Kubernetes authentication helper

### Using k tools

k tools supports environment variable chaining for easy namespace/context switching.

**Option 1: Inline variables** (one-off commands)
```bash
CONTAINER=matik-historian ENV=sandbox NAMESPACE=matik-sandbox AWS_PROFILE=airbnb-users-kubernetes k logs
```

**Option 2: Export variables** (persistent session)
```bash
export CONTAINER=matik-historian
export ENV=sandbox
export NAMESPACE=matik-sandbox
export AWS_PROFILE=airbnb-users-kubernetes

# Now you can run k commands directly
k logs
k describe
k exec -it -- /bin/bash
```
---

## 5. Optional: Productivity Tools

These tools enhance your workflow when working with Matik:

### Install Core Tools

```bash
# Install K9s (interactive Kubernetes CLI) and fzf (fuzzy finder)
brew install fzf derailed/k9s/k9s

# Install Excalibur (Airbnb Kubernetes helper)
airtool install excalibur
```

### Configure K9s

Add to your shell config (`~/.zshrc` or `~/.bashrc`):

```bash
export K9S_CONFIG_DIR=/Users/{your_ldap}/.k9s
```

### Recommended Aliases

Add these to your shell config for faster workflows:

```bash
# Quickly locate Kubernetes resources
alias kloc="excalibur kubectl loc --session=false --profile airbnb-users-kubernetes"

# Launch K9s in current namespace
alias kk='k9s -n "$(kubectl config view --minify --output jsonpath={..namespace})"'
```

**Usage**:
- `kloc` - Find and connect to Kubernetes resources interactively
- `kk` - Open K9s dashboard for current namespace

---

## Need Help?

If you encounter issues:
1. Verify all Gandalf permissions are approved
2. Check AWS credentials are configured for ProdEng account
3. Confirm LDAP group membership (for database access)
4. Contact the BizTech Operations Engineering team
