import { Button } from "@/components/ui/button";
import { getCurrentUser } from "@/services/clerk";
import { SignedIn, SignedOut, SignInButton, UserButton } from "@clerk/nextjs";
import Link from "next/link";
import { ReactNode, Suspense } from "react";
import { canAccessAdminPage } from "@/permissions/general";
import { Badge } from "@/components/ui/badge";

export default function AdminLayout({
    children
}: Readonly<{ children: ReactNode }>) {
    return (
        <>
            <Navbar />
            <main>{children}</main>
        </>
    )
}

function Navbar() {
    return (
        <header className="flex h-12 shadow 
        bg-background z-10">
            <nav className="flex gap-4 container"> 
                <div className="mr-auto flex items-center gap-2">
                    <Link className="mr-auto text-lg hover:underline" href="/">
                        AI AGT - Student
                    </Link>
                    <Badge>Admin</Badge>
                </div>
                    
                <AdminLink />
                <Link className="hover:bg-accent/10 flex items-center px-2" href="/admin/courses">
                    My Courses
                </Link>
                <Link className="hover:bg-accent/10 flex items-center px-2" href="/admin/products">
                    Products
                </Link>
                <Link className="hover:bg-accent/10 flex items-center px-2" href="/admin/sales">
                    Sales
                </Link>
                <div>
                    <UserButton appearance={{
                        elements: {
                            userButtonAvatarBox: { width: "100%", height: "100%" },
                            },
                        }}
                    ></UserButton>
                </div>
            </nav>
        </header>
    )
}

async function AdminLink() {
    const user = await getCurrentUser()
    // console.log("student/layout.tsx", user.user?.name)

    if(!canAccessAdminPage(user)) return null

    return (
        <Link className="hover:bg-accent/10 flex items-center px-2" href="/admin">
            Admin
        </Link>
    )
}
