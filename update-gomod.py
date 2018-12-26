# -*- coding: utf-8 -*-

import argparse
from collections import (
    namedtuple,
)
import json
import logging
import os
import pathlib
import re
import subprocess
import sys

import requests


least_go_version_str = '1.11.4'
# ensure go version >= `least_go_version_str`
res_go_version = subprocess.run(["go", "version"], stdout=subprocess.PIPE, encoding='utf-8')
if res_go_version.returncode != 0:
    raise Exception("failed to run `go version`")
res_go_version_str = res_go_version.stdout
ret = re.search(r"go\sversion\sgo([0-9]+\.[0-9]+\.[0-9]+)", res_go_version_str)
if ret is None:
    raise Exception("failed to parse the version from `go version`")
version_str = ret.groups(0)[0]
if version_str < least_go_version_str:
    raise Exception("go version should be >= {}".format(least_go_version_str))


GOPATH = os.getenv("GOPATH")
HOME = os.getenv("HOME")
TMP_GIT_REPO_PATH = "{}/.py-gx-gomod/gx-git-repos".format(HOME)
GX_PREFIX = "{}/src/gx/ipfs".format(GOPATH)

RepoVersion = namedtuple("RepoVersion", ["gx_hash", "git_repo", "version", "pkg"])

logger = logging.getLogger("update-gomod")


class DownloadFailure(Exception):
    pass


class GetCommitFailure(Exception):
    pass


class GetVersionFailure(Exception):
    pass


def make_gx_path(gx_hash, repo_name):
    return "{}/{}/{}".format(GX_PREFIX, gx_hash, repo_name)


def extract_gx_hash(gx_path):
    if not gx_path.startswith(GX_PREFIX):
        raise ValueError("gx_path={} should have the preifx {}".format(gx_path, GX_PREFIX))
    # get rid of the prefix f"{GX_PREFIX}/", and split with '/'
    path_list = gx_path[len(GX_PREFIX) + 1:].split('/')
    if len(path_list) < 2:
        raise ValueError("malform gx_path={}".format(gx_path))
    return path_list[0], path_list[1]


def make_git_repo_path(git_repo):
    return "{}/{}".format(TMP_GIT_REPO_PATH, git_repo)


def make_git_path(git_repo_path):
    return "{}/.git".format(git_repo_path)


def make_git_cmd(path, command):
    """Helper function to do git operations avoiding changing directories
    """
    return "git --git-dir={} {}".format(make_git_path(path), command)


def download_git_repo(git_repo):
    repo_path = make_git_repo_path(git_repo)
    pathlib.Path(repo_path).mkdir(parents=True, exist_ok=True)
    if not os.path.exists(make_git_path(repo_path)):
        git_url = "https://{}".format(git_repo)
        res = subprocess.run(
            "git clone {} {}".format(git_url, repo_path),
            shell=True,
        )
    else:
        res = subprocess.run(
            make_git_cmd(repo_path, "fetch"),
            shell=True,
        )
    if res.returncode != 0:
        raise DownloadFailure("failed to download/update the git repo: repo={}, res={}".format(
            git_repo,
            res,
        ))


def download_repos(git_repos):
    for git_repo in git_repos:
        download_git_repo(git_repo)


def is_version_in_repo(git_repo, sem_ver):
    git_repo_path = make_git_repo_path(git_repo)
    res = subprocess.run(
        "{}".format(
            make_git_cmd(git_repo_path, "tag")
        ),
        encoding='utf-8',
        shell=True,
        stdout=subprocess.PIPE,
    )
    if res.returncode != 0:
        raise GetVersionFailure("failed to access the repo: git_repo={}".format(git_repo))
    version_list = res.stdout.split('\n')
    return sem_ver in version_list


def get_commit_from_repo(git_repo, gx_hash):
    git_repo_path = make_git_repo_path(git_repo)
    res = subprocess.run(
        "{} | xargs {} | tail -n1".format(
            make_git_cmd(git_repo_path, "rev-list --all"),
            make_git_cmd(git_repo_path, "grep {}".format(gx_hash)),
        ),
        encoding="utf-8",
        shell=True,
        stdout=subprocess.PIPE,
    )
    if res.returncode != 0:
        raise GetCommitFailure("failed to fetch the commit: gx_hash={}, repo={}".format(
            gx_hash,
            git_repo,
        ))
    # success
    try:
        result = res.stdout
        module_commit = result.split(':')[0]
    except IndexError:
        raise GetCommitFailure(
            "fail to parse the commit: gx_hash={}, repo={}, result={}".format(
                gx_hash,
                git_repo,
                result,
            )
        )
    # check if `module_commit` is a commit hash, e.g. 5a13bddfa3a06705681ade8e1d4ea85374b6b12e
    try:
        int(module_commit, 16)
    except ValueError:
        return None
    if len(module_commit) != 40:
        return None
    return module_commit


def _remove_url_prefix(url):
    return re.sub(r"\w+://", "", url)


def _dvcsimport_to_git_repo(dvcsimport_path):
    # Assume `devcsimport_path` is in the format of `site/user/repo/package`.
    # Therefore, we only need to get rid of the `package`
    layered_paths = dvcsimport_path.split('/')
    return "/".join(layered_paths[:3])


def get_gxed_repos_from_github():
    # gxed_repos_api_url = "https://api.github.com/orgs/gxed/repos"
    # url = gxed_repos_api_url
    # data = []
    # while True:
    #     res = requests.get(url)
    #     data += res.json()
    #     # has more pages
    #     if "next" in res.links:
    #         url = res.links["next"]["url"]
    #     else:
    #         break
    # repo_map = {
    #     repo["name"]: _remove_url_prefix(repo["html_url"])
    #     for repo in data
    # }
    # FIXME: temporarily inline the result, to avoid API rate limit: 60 times per hour for
    #        non-authenticated requests
    repo_map = {'bbloom': 'github.com/gxed/bbloom', 'client_golang': 'github.com/gxed/client_golang', 'eventfd': 'github.com/gxed/eventfd', 'GoEndian': 'github.com/gxed/GoEndian', 'go-is-domain': 'github.com/gxed/go-is-domain', 'superrepo': 'github.com/gxed/superrepo', 'golang-levenshtein': 'github.com/gxed/golang-levenshtein', 'go-homedir': 'github.com/gxed/go-homedir', 'raft': 'github.com/gxed/raft', 'go-codec': 'github.com/gxed/go-codec', 'go-metrics': 'github.com/gxed/go-metrics', 'cli': 'github.com/gxed/cli', 'raft-boltdb': 'github.com/gxed/raft-boltdb', 'bolt': 'github.com/gxed/bolt', 'mux': 'github.com/gxed/mux', 'context': 'github.com/gxed/context', 'btcd': 'github.com/gxed/btcd', 'ed25519': 'github.com/gxed/ed25519', 's3gof3r': 'github.com/gxed/s3gof3r', 'bazil-fuse': 'github.com/gxed/bazil-fuse', 'mmap-go': 'github.com/gxed/mmap-go', 'errors': 'github.com/gxed/errors', 'smux': 'github.com/gxed/smux', 'websocket': 'github.com/gxed/websocket', 'badger': 'github.com/gxed/badger', 'go-lz4': 'github.com/gxed/go-lz4', 'btcutil': 'github.com/gxed/btcutil', 'protobuf': 'github.com/gxed/protobuf', 'go-farm': 'github.com/gxed/go-farm', 'go-immutable-radix': 'github.com/gxed/go-immutable-radix', 'golang-lru': 'github.com/gxed/golang-lru', 'sys': 'github.com/gxed/sys', 'ginkgo': 'github.com/gxed/ginkgo', 'gomega': 'github.com/gxed/gomega', 'base58': 'github.com/gxed/base58', 'sha256-simd': 'github.com/gxed/sha256-simd', 'blake2b-simd': 'github.com/gxed/blake2b-simd', 'opentracing-go': 'github.com/gxed/opentracing-go', 'go.uuid': 'github.com/gxed/go.uuid', 'go-check': 'github.com/gxed/go-check', 'pubsub': 'github.com/gxed/pubsub', 'hashland': 'github.com/gxed/hashland', 'zeroconf': 'github.com/gxed/zeroconf', 'dns': 'github.com/gxed/dns', 'go-net': 'github.com/gxed/go-net', 'go-text': 'github.com/gxed/go-text', 'go-crypto': 'github.com/gxed/go-crypto', 'go4-lock': 'github.com/gxed/go4-lock', 'go-isatty': 'github.com/gxed/go-isatty', 'go-colorable': 'github.com/gxed/go-colorable', 'structs': 'github.com/gxed/structs', 'go-toml': 'github.com/gxed/go-toml', 'toml': 'github.com/gxed/toml', 'pb': 'github.com/gxed/pb', 'go-runewidth': 'github.com/gxed/go-runewidth', 'color': 'github.com/gxed/color', 'tools': 'github.com/gxed/tools', 'jsondiff': 'github.com/gxed/jsondiff', 'ansi': 'github.com/gxed/ansi', 'go-multierror': 'github.com/gxed/go-multierror', 'go-errwrap': 'github.com/gxed/go-errwrap', 'fsnotify': 'github.com/gxed/fsnotify', 'fuse': 'github.com/gxed/fuse', 'go-crypto-dav': 'github.com/gxed/go-crypto-dav', 'bulb': 'github.com/gxed/bulb', 'sizedwaitgroup': 'github.com/gxed/sizedwaitgroup', 'go-git': 'github.com/gxed/go-git', 'go-diff': 'github.com/gxed/go-diff', 'warnings': 'github.com/gxed/warnings', 'gcfg': 'github.com/gxed/gcfg', 'ssh-agent': 'github.com/gxed/ssh-agent', 'go-billy': 'github.com/gxed/go-billy', 'uuid': 'github.com/gxed/uuid', 'go-nat': 'github.com/gxed/go-nat', 'goupnp': 'github.com/gxed/goupnp', 'backoff': 'github.com/gxed/backoff', 'go_rng': 'github.com/gxed/go_rng', 'go-junit-report': 'github.com/gxed/go-junit-report', 'go-require-gx': 'github.com/gxed/go-require-gx', 'opencensus-go': 'github.com/gxed/opencensus-go', 'go-shellwords': 'github.com/gxed/go-shellwords', 'grpc-go': 'github.com/gxed/grpc-go', 'oauth2': 'github.com/gxed/oauth2', 'mock': 'github.com/gxed/mock', 'glog': 'github.com/gxed/glog', 'go-genproto': 'github.com/gxed/go-genproto', 'google-cloud-go': 'github.com/gxed/google-cloud-go', 'google-api-go-client': 'github.com/gxed/google-api-go-client', 'go-sync': 'github.com/gxed/go-sync', 'pq': 'github.com/gxed/pq', 'prometheus-common': 'github.com/gxed/prometheus-common', 'client_model': 'github.com/gxed/client_model', 'httprouter': 'github.com/gxed/httprouter', 'go-gitignore': 'github.com/gxed/go-gitignore', 'aws-sdk-go': 'github.com/gxed/aws-sdk-go', 'go-jmespath': 'github.com/gxed/go-jmespath', 'envconfig': 'github.com/gxed/envconfig', 'go-ceph': 'github.com/gxed/go-ceph', 'testify': 'github.com/gxed/testify', 'gods': 'github.com/gxed/gods', 'go-buffruneio': 'github.com/gxed/go-buffruneio', 'ssh_config': 'github.com/gxed/ssh_config', 'go-flags': 'github.com/gxed/go-flags'}  # noqa: E501
    # FIXME: explicitly remove `bbloom`, since it cannot be found in `gxed/bbloom`,
    #        but can be found in `ipfs/bbloom`
    del repo_map['bbloom']
    return repo_map


def get_repo_deps(root_repo_path):
    """Go through the dependencies
    """
    visited_repos = set()
    queue = list()
    deps = []

    queue.append(root_repo_path)
    while len(queue) != 0:
        repo_path = queue.pop()
        if repo_path in visited_repos:
            continue
        package_file_path = "{}/package.json".format(repo_path)
        with open(package_file_path, 'r') as f_read:
            package_info = json.load(f_read)
        # add the deps
        if 'gxDependencies' in package_info:
            for dep_info in package_info['gxDependencies']:
                dep_name = dep_info['name']
                dep_gx_hash = dep_info['hash']
                dep_gx_path = make_gx_path(dep_gx_hash, dep_name)
                queue.append(dep_gx_path)
        visited_repos.add(repo_path)
        # avoid the `root_repo_path`, since `root_repo_path` might not necessarily be a gx package
        if repo_path != root_repo_path:
            pkg = package_info['gx']['dvcsimport']
            gx_hash, gx_pkg_name = extract_gx_hash(repo_path)
            # try to find the deps from the org gxed, to increase the probability to successfully
            # find the version
            # TODO: possibly make it to `git_repos` as a list, then we can try over all the options
            gxed_repo_map = get_gxed_repos_from_github()
            if gx_pkg_name in gxed_repo_map:
                git_repo = gxed_repo_map[gx_pkg_name]
            else:
                git_repo = _dvcsimport_to_git_repo(pkg)
            version = None
            if "version" in package_info:
                version = package_info["version"]
            rv = RepoVersion(gx_hash=gx_hash, git_repo=git_repo, version=version, pkg=pkg)
            deps.append(rv)
    # filter out non-github deps
    github_deps = [
        repo_version
        for repo_version in deps
        if "github.com" in repo_version.git_repo
    ]
    return github_deps


def parse_version_from_repo_gx_hash(git_repo, raw_version, repo_gx_hash):
    """dep to version or commit?
    """
    # TODO: add checks to ensure gx repos are downloaded(with `gx install`?)
    # try to find the version in the downloaded git repo
    commit = version = None

    if raw_version is None:
        version = None
    else:
        sem_ver = "v{}".format(raw_version)
        if is_version_in_repo(git_repo, sem_ver):
            version = sem_ver
    # try to find the commit in the downloaded git repo
    # XXX: will fail if the git repo does not contain information of gx_hash
    #      the usual case is, the repo is not maintained by IPFS teams
    commit = get_commit_from_repo(git_repo, repo_gx_hash)
    if version is not None and commit is None:
        logger.debug("can only find version of git_repo={} through versions, raw_version={}, repo_gx_hash={}".format(git_repo, raw_version, repo_gx_hash))  # noqa: E501
    return version, commit


def update_repo_to_go_mod(git_repo, version=None, commit=None):
    """Update the repo with either version or commit(priority: version > commit)
    """
    if version is not None:
        print("updating git_repo={} with version={} ...".format(git_repo, version))
        version_indicator = version
    elif commit is not None:
        print("updating git_repo={} with commit={} ...".format(git_repo, commit))
        version_indicator = commit
    else:
        raise ValueError("failed to update {}: version and commit are both None".format(git_repo))
    subprocess.run(
        "GO111MODULE=on go get {}@{}".format(git_repo, version_indicator),
        shell=True,
        stdout=subprocess.PIPE,
    )


def update_repos(root_repo_path, repos):
    os.chdir(root_repo_path)
    for repo_version in repos:
        gx_hash, git_repo, raw_version, _ = repo_version
        version, commit = parse_version_from_repo_gx_hash(git_repo, raw_version, gx_hash)
        try:
            update_repo_to_go_mod(git_repo, version, commit)
        except ValueError:
            # FIXME: ignore the failed updates for now
            print("failed to update repo_version={}".format(repo_version))
            logger.debug("failed to update the repo %s", git_repo)


def do_update(path):
    deps = get_repo_deps(path)
    update_repos(path, deps)


def do_download(path):
    deps = get_repo_deps(path)
    git_repo_list = [i.git_repo for i in deps]
    download_repos(git_repo_list)


modes = {
    "update": do_update,
    "download": do_download,
}


if __name__ == "__main__":
    supported_modes = ", ".join(modes.keys())
    parser = argparse.ArgumentParser(
        description='Resolve gx dependencies to git versions/commits',
    )
    parser.add_argument('mode', type=str, help="supported modes: {}".format(supported_modes))
    parser.add_argument(
        'path',
        type=str,
        help="root project directory, which contains `package.json`",
    )
    args = parser.parse_args()
    mode = args.mode
    path = args.path
    if mode not in modes:
        raise ValueError("Wrong mode={}. Supported: {}".format(
            mode,
            supported_modes,
        ))
    modes[mode](path)
