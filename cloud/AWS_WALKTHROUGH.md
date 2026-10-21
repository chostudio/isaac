# Isaac Sim on AWS: click-by-click walkthrough

For: a Mac (Apple Silicon, no NVIDIA GPU), AWS credits, first GPU instance. Everything below works from the AWS web console plus Terminal. CLI commands are marked "extra".
Facts were checked against official pages on 2026-10-20. Anything I could not verify is marked **[UNVERIFIED]**.

What you will end up with: one EC2 GPU box running Isaac Sim 6.2.0 in NVIDIA's container, streamed to your Mac. The planner defaults to Nemotron on NVIDIA's hosted API (free key from build.nvidia.com, nothing to install). Step 7 optionally adds Ollama for a fully local, keyless model on the same GPU.

---

## 0. Pick the plan (read this first)

| Decision | Choice | Why |
|---|---|---|
| Region | **us-east-1** (N. Virginia) or **us-west-2** (Oregon) | Both carry g6e; same price as far as I could verify. Use whichever has capacity. |
| Instance | **g6e.2xlarge** (1x L40S 48 GB VRAM, 8 vCPU, 64 GiB RAM) | AWS's own Isaac Sim page names g6e.2xlarge ([docs](https://docs.isaacsim.omniverse.nvidia.com/6.2.0/installation/install_advanced_cloud_setup_aws.html)). Isaac Sim lists 48 GB VRAM and 64 GB RAM as "ideal" ([requirements](https://docs.isaacsim.omniverse.nvidia.com/latest/installation/requirements.html)). |
| Cheaper fallback | g6e.xlarge (4 vCPU, 32 GiB RAM) | Meets Isaac's 32 GB RAM *minimum* only; RAM gets tight with Ollama. |
| Not recommended | g5.2xlarge (A10G, 24 GB) | Meets the 16 GB VRAM minimum but leaves little room for an 8B model beside Isaac Sim. Also not supported by the Marketplace AMI. |
| Image | **AWS Deep Learning Base GPU AMI (Ubuntu 24.04)** + Isaac Sim 6.2.0 container | Gets you the latest 6.x (container tag `6.2.0`). See step 3. |
| Disk | **300 GB gp3** | Container image + caches + 5 GB model. |

Estimated cost (on demand, Linux):

| Item | Price | Source |
|---|---|---|
| g6e.xlarge | $1.861/hr (us-east-1; us-west-2 listed the same by a third party) | [DoiT](https://compute.doit.com/spot/us-east-1/g6e.xlarge), [aws-pricing.com](https://aws-pricing.com/g6e.xlarge.html) |
| g6e.2xlarge | about $2.242/hr in both regions | [Devzero](https://www.devzero.io/instances/aws/g6e.2xlarge) (via search snippet) |
| g5.2xlarge | $1.212/hr (region not broken out) | [Vantage](https://instances.vantage.sh/aws/ec2/g5.2xlarge) |
| EBS gp3 | $0.08 per GB-month (the rate AWS uses in its own example; the page did not name a region) | [EBS pricing](https://aws.amazon.com/ebs/pricing/) |

- 300 GB gp3 is about $24/month, roughly $0.03/hr, billed whether the instance runs or not.
- **Plan for about $2.30/hr while running.** A 4-hour session is about $9.
- **[UNVERIFIED]** I could not load AWS's own price page (it is dynamic). Confirm in the console at launch (step 4) or in the [Pricing Calculator](https://calculator.aws/#/addService).
- Billing is per second, 60-second minimum ([AWS](https://aws.amazon.com/ec2/pricing/on-demand/)).
- **A STOPPED instance still bills for its EBS volume.** Only *terminating* stops all of it. Stopping ends compute charges ([Marketplace listing](https://aws.amazon.com/marketplace/pp/prodview-bl35herdyozhw) says storage may still bill).

### 0.1 Set a budget alert (do this before anything else)

1. Sign in at https://console.aws.amazon.com/ and open **Billing and Cost Management** (search bar, top).
2. Left menu: **Budgets** then **Create budget**.
3. Choose **Use a template (simplified)** then **Monthly cost budget**.
4. Name it `isaac-budget`, set the amount to something like `50` (USD), enter your email, click **Create budget**.
5. Repeat with **Zero spend budget** if you want an email the moment anything is charged beyond credits.
6. Steps per [AWS Budgets docs](https://docs.aws.amazon.com/cost-management/latest/userguide/budgets-create.html).

You should see: `isaac-budget` listed under Budgets, and a confirmation email.

**How credits apply:** credits are applied automatically on your invoice to eligible charges. Check eligible services under Billing, **Credits**. **[UNVERIFIED]** Budgets may count cost before credits, so alerts can fire even when credits cover the bill. Treat the alert as "you are burning credits." Marketplace software is free here, but some third-party Marketplace charges are not credit-eligible; the Isaac Sim AMI itself costs $0 ([listing](https://aws.amazon.com/marketplace/pp/prodview-bl35herdyozhw)).

---

## 1. Request GPU quota (do this early, it is the slow step)

New accounts usually have a **0** limit for GPU instances. Quotas are **per region**: request it in the same region you will launch in.

1. In the console search bar, type `Service Quotas`, open it.
2. Top-right region dropdown: choose **US East (N. Virginia)** (or Oregon).
3. Left menu: **AWS services** then click **Amazon Elastic Compute Cloud (Amazon EC2)**.
4. In the filter box type `G and VT`. Click **Running On-Demand G and VT instances**.
5. Click **Request increase at account level** (the button may read **Request quota increase**).
6. **Increase quota value:** `8` (g6e.2xlarge = 8 vCPUs; the quota counts vCPUs, not instances). Request `16` if you want room for a bigger size later; smaller requests tend to be approved faster **[UNVERIFIED]**.
7. Click **Request**.
8. Steps per [EC2 quotas docs](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/ec2-resource-limits.html).

You should see: status **Pending**, then **Quota requested** with a Support case number (Dashboard in the left menu). Approval is often minutes to a day or two. AWS does not publish a guaranteed time **[UNVERIFIED]**. Do not buy time: if it is still pending after 24 hours, reply on the Support case.

If AWS asks for a justification (or you reply to the case), write something like:

> I am a student building a robotics simulation project using NVIDIA Isaac Sim, which requires an RTX-class GPU (L40S). I need 8 vCPUs of On-Demand G instances (one g6e.2xlarge) in us-east-1 for short, interactive sessions. I have a budget alert set and will stop the instance when idle.

Also check **All G and VT Spot Instance Requests** is not what you want: spot can be interrupted mid-session. Use On-Demand.

---

## 2. Create a key pair (the .pem file)

1. Console search: `EC2`, open it. Check the region (top right) matches your quota region.
2. Left menu: **Network & Security** then **Key Pairs** then **Create key pair**.
3. Name `isaac-key`, type **RSA**, format **.pem**, click **Create key pair**.
4. Your browser downloads `isaac-key.pem`. You cannot download it again.
5. In Terminal on your Mac:
   ```bash
   mkdir -p ~/.ssh && mv ~/Downloads/isaac-key.pem ~/.ssh/
   chmod 400 ~/.ssh/isaac-key.pem
   ```

You should see: `ls -l ~/.ssh/isaac-key.pem` shows `-r--------`. (SSH refuses keys with looser permissions.)

---

## 3. Choose the image

| Option | Verdict |
|---|---|
| **AWS Marketplace "NVIDIA Isaac Sim Development Workstation (Linux)"** | Exists and is **free** (you pay EC2 only). Ubuntu 24.04, listing version `2026.3.0`, supports g6e/g7e/g7 only, **minimum 512 GiB** storage, needs a free NVIDIA Developer Program membership, and is set up around NICE DCV desktop. [Listing](https://aws.amazon.com/marketplace/pp/prodview-bl35herdyozhw). **[UNVERIFIED]** which Isaac Sim version that AMI contains; neither the listing nor AWS docs say. |
| **Deep Learning Base GPU AMI (Ubuntu 24.04) + container** | **Recommended.** You choose the exact tag (`6.2.0`, the latest at time of writing per the [download page](https://docs.isaacsim.omniverse.nvidia.com/latest/installation/download.html)), 300 GB is enough, and the container is what Isaac documents for streaming. Name format: *Deep Learning Base OSS Nvidia Driver GPU AMI (Ubuntu 24.04) YYYYMMDD* ([DLAMI docs](https://docs.aws.amazon.com/dlami/latest/devguide/aws-deep-learning-x86-base-gpu-ami-ubuntu-24-04.html)). |

**[UNVERIFIED]** that this DLAMI ships Docker and the NVIDIA Container Toolkit. The page I could read did not list contents. Step 6 checks it and gives the install commands if missing.
**[UNVERIFIED]** driver match: Isaac Sim 6.x was *tested* on Linux driver 595.58.03; that is a tested version, not a stated minimum ([requirements](https://docs.isaacsim.omniverse.nvidia.com/latest/installation/requirements.html)). Step 6 prints your driver version; see Troubleshooting if Isaac refuses to start.

Fallback if the container route fights you: launch the Marketplace AMI instead (same steps, 512 GiB disk, open port 8443 too) and follow its [AWS doc](https://docs.isaacsim.omniverse.nvidia.com/6.2.0/installation/install_advanced_cloud_setup_aws.html): `sudo passwd ubuntu`, then `cd ~/IsaacSim && ./post_install.sh && ./warmup.sh && ./isaac-sim.sh` over DCV (warm-up takes 15+ minutes).

---

## 4. Launch the instance

1. EC2 console, **Instances** then **Launch instances** (orange).
2. **Name:** `isaac-box`.
3. **Application and OS Images:** click **Browse more AMIs**, tab **AWS Marketplace AMIs** or **Quick Start**; search `Deep Learning Base OSS Nvidia Driver GPU AMI (Ubuntu 24.04)`, click **Select** (choose the newest date).
4. **Instance type:** open the dropdown, search `g6e.2xlarge`. The panel shows the on-demand price: check it against the table above. If it is greyed out or you get a quota error later, see Troubleshooting.
5. **Key pair:** choose `isaac-key`.
6. **Network settings:** click **Edit**. Leave VPC/subnet default. **Auto-assign public IP: Enable.**
7. **Firewall:** choose **Create security group**, name `isaac-sg`. Delete any default rule and add these (ports from the [AWS](https://docs.isaacsim.omniverse.nvidia.com/6.2.0/installation/install_advanced_cloud_setup_aws.html) and [container](https://docs.isaacsim.omniverse.nvidia.com/latest/installation/install_container.html) docs). For every rule set **Source type: My IP**:

   | Type | Protocol | Port | Purpose |
   |---|---|---|---|
   | SSH | TCP | 22 | your terminal |
   | Custom TCP | TCP | 49100 | WebRTC signaling |
   | Custom UDP | UDP | 47998 | WebRTC video |
   | Custom TCP | TCP | 8210 | browser viewer (optional; only if you use the Docker Compose viewer) |

   The streaming ports have **no authentication or encryption**, so never use 0.0.0.0/0. Do not open 11434 (Ollama) at all; it stays on the box.
8. **Configure storage:** change the root volume to **300 GiB**, type **gp3**. Leave **Delete on termination = Yes** (so terminating really stops all billing).
9. Right panel: confirm **Number of instances = 1**, click **Launch instance**.

You should see: "Successfully initiated launch of instance". In **Instances**, wait for **Instance state: Running** and **Status check: 2/2 checks passed** (about 2 to 3 minutes). Click the instance and copy its **Public IPv4 address**.

Your public IP changes every time you stop/start, and **My IP** rules go stale if your home IP changes (café Wi-Fi!). Fix: edit the security group (EC2, **Security Groups**, select it, **Edit inbound rules**, re-pick **My IP**).

Extra (CLI, optional): `aws ec2 run-instances --instance-type g6e.2xlarge --key-name isaac-key ...` is possible, but the console is easier for a first run.

---

## 5. Connect from the Mac

```bash
ssh -i ~/.ssh/isaac-key.pem ubuntu@<PUBLIC_IP>
```

Type `yes` on the fingerprint prompt. You should see an `ubuntu@ip-...` prompt. (Default user on Ubuntu AMIs is `ubuntu`.)

Check the GPU:

```bash
nvidia-smi
```

You should see a table with **NVIDIA L40S** and about 46 GB of memory, plus a driver version. Write the driver version down.

---

## 6. Prepare Docker and the Isaac Sim container

Commands from the [container install guide](https://docs.isaacsim.omniverse.nvidia.com/latest/installation/install_container.html).

1. Check Docker and the GPU toolkit:
   ```bash
   docker --version
   docker run --rm --gpus all nvcr.io/nvidia/cuda:12.8.0-base-ubuntu24.04 nvidia-smi
   ```
   You should see the same L40S table printed from inside a container. If `docker` is missing or this fails, install (also from the docs):
   ```bash
   curl -fsSL https://get.docker.com -o get-docker.sh && sudo sh get-docker.sh
   sudo usermod -aG docker $USER      # then log out and back in
   sudo apt-get install -y nvidia-container-toolkit
   sudo nvidia-ctk runtime configure --runtime=docker
   sudo systemctl restart docker
   ```
2. **Get an NGC API key** (on your Mac, in a browser; [NGC guide](https://docs.nvidia.com/ngc/gpu-cloud/ngc-user-guide/index.html)):
   1. Go to https://ngc.nvidia.com/signin and sign in (free account).
   2. Top-right user icon, **Account Settings** then **Generate API Key** then **+ Generate Personal Key**.
   3. Name it, pick an expiration, tick the services it offers (Catalog / NGC Catalog), click **Generate Personal Key**.
   4. Copy the key now; it is shown once.
   - The exact menu names change; if they differ, follow the NGC guide linked above. **[UNVERIFIED]** whether the Isaac Sim image needs the key at all (the docs show a plain `docker pull`); logging in is harmless.
3. Log in on the instance:
   ```bash
   docker login nvcr.io --username '$oauthtoken'
   ```
   Type `$oauthtoken` exactly (single quotes), paste the key as the password. You should see `Login Succeeded`.
4. Pull the image (about 20+ GB **[UNVERIFIED size]**, several minutes):
   ```bash
   docker pull nvcr.io/nvidia/isaac-sim:6.2.0
   ```
   Check for a newer 6.x tag at https://catalog.ngc.nvidia.com/orgs/nvidia/containers/isaac-sim (I could not load that catalog page; 6.2.0 comes from the install docs).
5. Create the cache folders (speeds up restarts):
   ```bash
   mkdir -p ~/docker/isaac-sim/cache/{main,computecache,kit} ~/docker/isaac-sim/{config,data,logs,pkg} ~/.cache/ov/hub
   sudo chown -R 1234:1234 ~/docker/isaac-sim ~/.cache/ov/hub
   ```

---

## 7. Optional: install Ollama for a free local model (on the host, not in the container)

Skip this if you use the default Nemotron planner; just pass your key into the container in step 8.

1. ```bash
   curl -fsSL https://ollama.com/install.sh | sh
   ```
   (From the [Ollama Linux docs](https://docs.ollama.com/linux); it creates a systemd service.)
2. Pull a tool-calling model:
   ```bash
   ollama pull qwen3:8b
   ```
   `qwen3:8b` is about 5.2 GB (Q4) and lists the **tools** capability ([library page](https://ollama.com/library/qwen3:8b)). Alternative: `llama3.1:8b` (**[UNVERIFIED]** its tool support and size; I did not check).
3. Test it:
   ```bash
   ollama run qwen3:8b "say hi"      # exit with /bye
   curl http://localhost:11434/api/tags
   ```
   You should see a reply, then JSON listing `qwen3:8b`. `nvidia-smi` shows about 5 to 6 GB used while it is loaded.
4. **Reaching it from the Isaac container:** the `docker run` in step 8 uses `--network=host`, so inside the container `http://localhost:11434` is the host's Ollama. Nothing else to configure. (Ollama listens on localhost:11434 by default **[UNVERIFIED in the page I read]**; do not change it to 0.0.0.0, which would be a security risk with no firewall rule protecting it anyway.)
5. Run the agent with `--provider ollama` to use it (model `qwen3:8b`, via `http://localhost:11434/v1`). No API key needed.

Ollama unloads models after idle time and reloads on demand, so VRAM use is not constant **[UNVERIFIED default timeout]**.

---

## 8. Start Isaac Sim and stream it to the Mac

### 8.1 Open the security group check

Confirm the three rules from step 4 show **My IP** (EC2, Security Groups, `isaac-sg`, **Inbound rules**). Check it again whenever your Wi-Fi changes.

### 8.2 Start the container with a shell

Run this on the instance (from the [docs](https://docs.isaacsim.omniverse.nvidia.com/latest/installation/install_container.html)). I added `ISAACSIM_HOST` (the docs: "public or private IP of the host for livestream", default `127.0.0.1`) and the project mount. Put your instance's **public IP** in `ISAACSIM_HOST`. **[UNVERIFIED]** whether public or private IP is right on EC2; the AWS page passes the public IP via `publicIp=` flags, so use the public IP.

```bash
git clone <your-repo-url> ~/isaac-franka-agent   # or scp, step 9

docker run --name isaac-sim --entrypoint bash -it --gpus all -e "ACCEPT_EULA=Y" --rm --network=host \
    -e "PRIVACY_CONSENT=N" -e "ISAACSIM_HOST=<PUBLIC_IP>" -e "NVIDIA_API_KEY=$NVIDIA_API_KEY" \
    -v ~/docker/isaac-sim/cache/main:/isaac-sim/.cache:rw \
    -v ~/docker/isaac-sim/cache/computecache:/isaac-sim/.nv/ComputeCache:rw \
    -v ~/docker/isaac-sim/cache/kit:/isaac-sim/kit/cache:rw \
    -v ~/docker/isaac-sim/logs:/isaac-sim/.nvidia-omniverse/logs:rw \
    -v ~/docker/isaac-sim/config:/isaac-sim/.nvidia-omniverse/config:rw \
    -v ~/docker/isaac-sim/data:/isaac-sim/.local/share/ov/data:rw \
    -v ~/docker/isaac-sim/pkg:/isaac-sim/.local/share/ov/pkg:rw \
    -v ~/.cache/ov/hub:/var/cache/hub:rw \
    -v ~/isaac-franka-agent:/workspace/isaac-franka-agent \
    -u 1234:1234 \
    nvcr.io/nvidia/isaac-sim:6.2.0
```

(`PRIVACY_CONSENT=Y` in the docs opts you into data collection; I set `N`. `--network=host` is required for WebRTC.) You should get a bash prompt inside the container.

### 8.3 First test: the stock streaming app

```bash
./runheadless.sh -v
```

Wait for the log line **`Isaac Sim Full Streaming App is loaded.`** The first run compiles shaders and can take several minutes. Leave it running.

### 8.4 Install the Mac client and connect

1. On your Mac, open https://docs.isaacsim.omniverse.nvidia.com/latest/installation/download.html and under **Latest Release** download **Isaac Sim WebRTC Streaming Client** (version 2.0.0 listed; there are separate Apple silicon `.dmg` and Intel `.dmg` builds). Apple Silicon Mac: choose the **aarch64** `.dmg`.
2. Open the `.dmg`, drag the app into **Applications**, open it (right-click, **Open** the first time if macOS blocks it).
3. Enter the instance's **public IP** and click **Connect**. If the window stays blank, use **View** then **Reload** ([client docs](https://docs.isaacsim.omniverse.nvidia.com/latest/installation/manual_livestream_clients.html)). Only one client can connect at a time.

You should see: the Isaac Sim editor UI in the client window.

### 8.5 Run the project with streaming

Stop the stock app (Ctrl+C), then:

```bash
./python.sh -m pip install anthropic       # needed again each time a --rm container starts
export ANTHROPIC_API_KEY=...
./python.sh standalone_examples/api/isaacsim.robot.manipulators/franka/pick_place.py
```

`run_isaac.py` is written against the Isaac Sim 6.2.0 source (it follows `standalone_examples/api/isaacsim.robot_motion.examples/manipulation/stacking.py`), so it matches the `6.2.0` container. Run the stock example first as a sanity check: `./python.sh standalone_examples/api/isaacsim.robot_motion.examples/manipulation/stacking.py --headless --test` should print `PASS`.

**Streaming the project script:** `run_isaac.py --livestream` uses the same launch config as the 6.2 `livestream.py` sample (headless app plus the `omni.kit.livestream.app` extension). Add the remote-streaming flags from the [docs](https://docs.isaacsim.omniverse.nvidia.com/latest/installation/manual_livestream_clients.html):

```bash
./python.sh /workspace/isaac-franka-agent/isaac/run_isaac.py --livestream \
    --/exts/omni.kit.livestream.app/primaryStream/publicIp=<PUBLIC_IP> \
    --/exts/omni.kit.livestream.app/primaryStream/signalPort=49100 \
    --/exts/omni.kit.livestream.app/primaryStream/streamPort=47998
```

Then connect the Streaming Client to `<PUBLIC_IP>`.

### 8.6 If WebRTC will not connect: alternatives

- **Browser viewer (recommended by Isaac for cloud):** Isaac's Docker Compose web viewer serves at `http://<PUBLIC_IP>:8210` in Chrome or another Chromium browser, no client install; Ubuntu hosts only ([docs](https://docs.isaacsim.omniverse.nvidia.com/latest/installation/manual_livestream_clients.html)). Needs TCP 8210 open to My IP.
- **NICE DCV desktop:** only comes preinstalled on the Marketplace AMI (TCP 8443). Use the fallback in step 3.
- **Headless + record to file:** run with `--headless`, have the script save frames or use Isaac's recorder, then `scp` the mp4 to your Mac. **[UNVERIFIED]** I did not check Isaac's recording extension; simplest is capturing camera frames and encoding with `ffmpeg`.

---

## 9. Get the project onto the instance

Pick one.

**git (simplest):** on the instance, `git clone <your-repo-url> ~/isaac-franka-agent`.

**scp from your Mac** (run in a *Mac* Terminal, not over SSH):

```bash
scp -i ~/.ssh/isaac-key.pem -r ~/Desktop/isaac-franka-agent ubuntu@<PUBLIC_IP>:~/
```

The folder is mounted into the container at `/workspace/isaac-franka-agent` by the `docker run` in step 8.2. Do not copy your API key into the repo; set it with `export` inside the container.

Run command from the README, adapted to the container:

```bash
cd /isaac-sim
./python.sh -m pip install openai

# 1. No LLM: three scripted moves. Must print SMOKE PASS before going further.
./python.sh /workspace/isaac-franka-agent/isaac/run_isaac.py --smoke --headless

# 2. With the free local model (add the livestream flags from 8.5 to watch it)
./python.sh /workspace/isaac-franka-agent/isaac/run_isaac.py --livestream \
    --commands "put the red cube on the blue one" "now build a tower with green on the bottom"
```

You should see: the Franka arm moving cubes in the streamed viewport, with each verified step printed in the terminal. The `pip install openai` must be repeated whenever you start a fresh container.

---

## 10. Shutdown checklist

1. In the container, Ctrl+C your script, then `exit` (the `--rm` flag deletes the container; the host folders keep caches).
2. Copy off anything you want (videos, logs):
   ```bash
   scp -i ~/.ssh/isaac-key.pem ubuntu@<PUBLIC_IP>:~/some-file.mp4 ~/Desktop/
   ```
3. **Stop** (pausing between sessions) or **Terminate** (done for good):
   - EC2 console, **Instances**, tick `isaac-box`, **Instance state** then **Stop instance** or **Terminate (delete) instance**.
   - **Stop:** compute billing ends; the 300 GB volume (about $24/month) keeps billing; the public IP changes on next start ([AWS](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/Stop_Start.html)); the Ollama model and Docker caches survive.
   - **Terminate:** everything including the root volume is deleted (if Delete on termination was Yes). Re-doing setup costs about 30 minutes.
   - Rule of thumb: stop if you will be back within about a week; terminate otherwise.
4. Confirm nothing is billing:
   - EC2 console, left menu **Volumes**: no leftover volumes (after terminate). **Elastic IPs**: none (none were allocated here).
   - Open https://console.aws.amazon.com/ec2globalview/home (EC2 Global View) to see running and stopped instances in *all* regions.
   - **Billing and Cost Management** then **Bills** the next day to confirm charges stopped.
5. Optional safety net: `sudo shutdown -h +120` on the instance schedules a stop in 2 hours (an OS shutdown stops the instance by default, per the same AWS doc).

Extra (CLI): `aws ec2 stop-instances --instance-ids i-...`.

---

## 11. Troubleshooting

**Quota request pending or denied**
- Pending: wait; check Service Quotas **Dashboard**; reply on the Support case.
- Denied or "contact support": open the case, restate the justification from step 1, ask for a smaller value (4 for a g6e.xlarge), or try another region (quotas are per region).
- Launch error "You have requested more vCPU capacity than your current vCPU limit": quota not applied yet or in the wrong region (check the region dropdown).
- If it stalls for days: NVIDIA Brev is the other route listed in `cloud/SETUP.md`.

**InsufficientInstanceCapacity**
- Launch again, but at step 4 under **Network settings** pick a different **Subnet** (a different Availability Zone, for example `us-east-1b`).
- Try the other region (needs its own quota), or try g6e.xlarge, or wait 15 to 30 minutes.

**Black viewport or client blank**
- Wait for "Full Streaming App is loaded" (first start is slow because of shaders).
- Client: **View** then **Reload**; only one client at a time; quit other sessions.
- Check both TCP 49100 and **UDP 47998** are open to your *current* IP (opening only TCP is not enough, per the docs).
- Confirm `ISAACSIM_HOST` is the public IP and `--network=host` is on the `docker run`.
- Python scripts started headless produce no viewport; use the streaming config (step 8.5).

**Driver mismatch / container GPU errors**
- `nvidia-smi` fails on the host: the AMI driver did not load; reboot the instance (EC2, **Instance state**, **Reboot**). Make sure you launched a *GPU* AMI on a GPU instance.
- Docker cannot see the GPU: rerun the toolkit install in step 6.1.
- Isaac complains about driver version: compare to tested 595.58.03 (Linux). NVIDIA says to use the Latest Production Branch driver from the Unix driver archive ([requirements](https://docs.isaacsim.omniverse.nvidia.com/latest/installation/requirements.html)). Alternatively use the Marketplace AMI (step 3 fallback).
- Out of GPU memory: stop Ollama's model (`ollama stop qwen3:8b`) or use a smaller model/quantization.

**SSH problems**
- `WARNING: UNPROTECTED PRIVATE KEY FILE`: run `chmod 400 ~/.ssh/isaac-key.pem`.
- Timeout: your IP changed (fix the security group) or you used the old public IP after a restart.

---

## Sources

- Isaac Sim requirements: https://docs.isaacsim.omniverse.nvidia.com/latest/installation/requirements.html
- Container install: https://docs.isaacsim.omniverse.nvidia.com/latest/installation/install_container.html
- AWS deployment: https://docs.isaacsim.omniverse.nvidia.com/6.2.0/installation/install_advanced_cloud_setup_aws.html
- Livestream clients: https://docs.isaacsim.omniverse.nvidia.com/latest/installation/manual_livestream_clients.html
- Downloads: https://docs.isaacsim.omniverse.nvidia.com/latest/installation/download.html
- Marketplace listing: https://aws.amazon.com/marketplace/pp/prodview-bl35herdyozhw
- EC2 quotas: https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/ec2-resource-limits.html
- Budgets: https://docs.aws.amazon.com/cost-management/latest/userguide/budgets-create.html
- Stop/start: https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/Stop_Start.html
- DLAMI Ubuntu 24.04: https://docs.aws.amazon.com/dlami/latest/devguide/aws-deep-learning-x86-base-gpu-ami-ubuntu-24-04.html
- Ollama: https://docs.ollama.com/linux and https://ollama.com/library/qwen3:8b
