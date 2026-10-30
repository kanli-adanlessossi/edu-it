# Lab_Image: Linux base. Alpine 3.21 pinned by immutable multi-arch index digest.
# Bump procedure: docs/LABS.md. No secrets, Check_Bundle, or solutions belong here.
FROM alpine@sha256:ee16d92f96db08d9d2d07da48555a27f3a69d8f0759021dbc601195ed0d2b5bd

# Build-time package mirror only; default is the CDN. The final image's /etc/apk/repositories is untouched.
ARG ALPINE_MIRROR=https://dl-cdn.alpinelinux.org/alpine
RUN printf '%s\n' "$ALPINE_MIRROR/v3.21/main" "$ALPINE_MIRROR/v3.21/community" > /tmp/apk-repositories \
    && apk add --no-cache --repositories-file /tmp/apk-repositories bash coreutils findutils tar procps \
    && rm -f /tmp/apk-repositories \
    # Users: learner (1000) works in the lab, grader (1001) runs Checks.
    && addgroup -g 1000 learner \
    && adduser -D -u 1000 -G learner -s /bin/bash learner \
    && addgroup -g 1001 grader \
    && adduser -D -u 1001 -G grader -s /bin/bash grader \
    # Group devs is baked in because the root filesystem is read-only at run time.
    && addgroup -g 2000 devs \
    && addgroup learner devs \
    # No setuid or setgid binaries.
    && find / -xdev -type f \( -perm -4000 -o -perm -2000 \) -exec chmod a-s {} + \
    && mkdir -p /workspace /grader \
    && chown learner:learner /workspace \
    && chown root:root /grader && chmod 0700 /grader

WORKDIR /workspace
USER learner
CMD ["/bin/bash"]
