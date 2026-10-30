# Lab_Image: Python. Built on linux-base through the bake named context "linux-base"
# (see docker-bake.hcl), so the only pinned digest lives in linux-base/Dockerfile.
FROM linux-base

USER root
ARG ALPINE_MIRROR=https://dl-cdn.alpinelinux.org/alpine
RUN printf '%s\n' "$ALPINE_MIRROR/v3.21/main" "$ALPINE_MIRROR/v3.21/community" > /tmp/apk-repositories \
    && apk add --no-cache --repositories-file /tmp/apk-repositories python3 \
    && rm -f /tmp/apk-repositories \
    && find / -xdev -type f \( -perm -4000 -o -perm -2000 \) -exec chmod a-s {} +

WORKDIR /workspace
USER learner
CMD ["/bin/bash"]
