import {z } from "zod"

export const CourseSchema = z.object({
    name: z.string().min(1, "Required"),
    description: z.string().min(1, "Required"), 
})