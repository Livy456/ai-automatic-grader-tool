
import { db } from "@/drizzle/db"
import { UserRole, UserTable } from "@/drizzle/schema"
import { getUserIdTag } from "@/features/users/db/cache"
import { auth, clerkClient } from "@clerk/nextjs/server"
import { eq } from "drizzle-orm/sql/expressions/conditions"
import { cacheTag } from "next/dist/server/use-cache/cache-tag"

const client = await clerkClient()

export async function getCurrentUser({allData = false}: {allData?: boolean} = {}) {
    const {userId, sessionClaims, redirectToSignIn} = await auth()
    
    return {
        clerkUserId: userId,
        userId: sessionClaims?.dbId,
        role: sessionClaims?.role,
        user: 
            allData && sessionClaims?.dbId != null 
                ? await getUser(sessionClaims.dbId) 
                : undefined,
        redirectToSignIn
    }
}
// sync user metadata in clerk with database info
export async function syncClerkUserMetadata(user: {
    id: string, 
    clerkUserId: string,
    role: UserRole
}) {
    return client.users.updateUserMetadata(user.clerkUserId, {
        publicMetadata: {
            dbId: user.id,
            role: user.role,
        }
    })
}

// since using cache must be async function
// caches the information after you first create or update the user, so you don't have to recall it
async function getUser(id: string) {
    "use cache"
    cacheTag(getUserIdTag(id))
    // console.log("Called")

    return db.query.UserTable.findFirst({
        where: eq(UserTable.id, id),
    })
}