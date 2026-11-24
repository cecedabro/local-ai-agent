# cv-gpu-link: a kind sidecar that joins the tailnet as an ephemeral tag:cv-skills-client node and forwards
# plain HTTP on LINK_LISTEN (default 127.0.0.1:8088) to cv-skills-gpu:80. Userspace networking: no NET_ADMIN, no tun.
FROM tailscale/tailscale:v1.104.7
RUN apk add --no-cache socat
COPY entrypoint.sh /usr/local/bin/cv-gpu-link
USER 1000
ENTRYPOINT ["/usr/local/bin/cv-gpu-link"]
