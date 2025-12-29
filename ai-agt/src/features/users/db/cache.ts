import { revalidateTag } from "next/cache"
import { cacheTag } from "next/dist/server/use-cache/cache-tag"
import { getIdTag, getGlobalTag } from "@/lib/dataCache"
import { get } from "http"

// resets cache everytime we access our data
export function getUserGlobalTag() {
    return getGlobalTag("users")
}

export function getUserIdTag(id: string) {

    return getIdTag("users", id)
}

export function revalidateUserCache(id: string) {

    const user_tag = getUserGlobalTag()
    const user_IdTag = getUserIdTag(id)

    // invalidates the cache for the given tag at page-level
    revalidateTag(user_tag, "page")
    revalidateTag(user_IdTag, "page")

    // invalidates the cache for the given tag at layout-level
    revalidateTag(user_tag, "layout")
    revalidateTag(user_IdTag, "layout")
}

function getUser(id: string)
{
    "use cache"
    cacheTag(getUserIdTag(id))
}

function getAllUsers()
{
    "use cache"
    cacheTag(getUserGlobalTag())
}