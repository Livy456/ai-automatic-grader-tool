"use client"
import { useForm } from "react-hook-form"
import { zodResolver } from "@hookform/resolvers/zod"
import { z } from "zod"

export function CourseForm()
{
    const form = useForm({
        resolver: zodResolver(z.object({})),
    })
    return (
        <div className="border p-4 rounded-md">
        </div>
    )
}