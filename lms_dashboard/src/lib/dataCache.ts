type CACHE_TAG = "products" | "users"

export function getGlobalTag(tag: CACHE_TAG) {
    return `global:${tag}` as const
}

// can cache by id for any given tag
export function getIdTag(tag: CACHE_TAG, id: string) {
    return `$id:${id}-${tag}` as const
}

// can caches all user data
export function getUserTag(tag: CACHE_TAG, userId: string) {
    return `user:${userId}-${tag}` as const
}

// can cache all course data
export function getCourseTag(tag: CACHE_TAG, courseId: string) {
    return `course:${courseId}-${tag}` as const
}
